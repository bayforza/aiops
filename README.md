# AIOps Copilot: Community Edition

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Kurum içinde çalışan, **salt okuma** bir incident copilot. Alertmanager'dan gelen alert'i alır;
Kubernetes, Prometheus, Loki ve Git'ten bağlamı toplar; kök nedeni **yerel bir LLM** ile çıkarır;
düzeltmeyi GitOps reposuna **pull request** olarak önerir. Hiçbir şeyi kendisi çalıştırmaz ve merge etmez.

```
Alertmanager ──webhook──▶ Copilot ──▶ Kubernetes / Prometheus / Loki / Git   (salt okuma)
                              │
                              ├─ Drain3 log şablonları + kural tabanlı sinyaller (CPU, ms)
                              ├─ Yerel LLM (Ollama / llama.cpp / vLLM, OpenAI uyumlu API)
                              ├─ Kanıt skoru → belirsizse anonim özetle cloud ikinci görüş (opsiyonel, varsayılan kapalı)
                              ├─ Runbook seçimi  →  GitOps patch  →  PR (insan onaylar)
                              └─ Hash zincirli audit log  +  Slack/Teams bildirimi
```

## Tasarım ilkeleri

- **Önce kurallar, sonra model.** OOMKilled, ImagePullBackOff, CreateContainerConfigError gibi kesin
  sinyaller kurallarla bulunur. Model bunları özetler ve kuralların kaçırdığını (ör. YAML hatası,
  DNS hatası) logdan okur. Model kesin bir kuralla çelişirse güven düşürülür ve PR açılmaz.
- **Küçük bağlam.** 500 satır log, Drain3 ile 5-20 şablona iner; prompt ~1.500 token. CPU'da çalışabilmenin anahtarı bu.
- **Model fix uydurmaz.** Model sadece `runbooks/` altındaki onaylı runbook'lardan birini seçer;
  patch'i kod üretir (`increase_memory_limit`, `rollback_image`).
- **Kademeli otonomi.** `autonomy_level: 0` sadece bildirir ve diff önizlemesi yazar.
  `1` ise güven eşiğini geçerse PR açar. Doğrudan çalıştırma (seviye 2+) bilerek yok.
- **Denetlenebilirlik.** Her adım `out/audit.jsonl` içine hash zincirli yazılır:
  `aiops-copilot audit-verify` ile bütünlüğü kontrol edilir.

## Hibrit AI: yerel önce, cloud sadece gerektiğinde

Modelin kendi söylediği güvene tek başına güvenilmez. `confidence.py` toplanan kanıta bakarak ayrı bir
**kanıt skoru** hesaplar: kategoriyi destekleyen kesin bir sinyal var mı, Kubernetes / metrik / log gibi
bağımsız kaynaklar aynı nedeni gösteriyor mu, kural tabanlı analiz ve model aynı fikirde mi.

`routing.py` ikinci görüşün gerekip gerekmediğine karar verir (sırayla):

| Durum | Yol |
|---|---|
| `cloud.enabled: false` (varsayılan) | yerel |
| namespace `local_only_namespaces` içinde | sadece yerel |
| alert `data_classification=restricted/confidential` | sadece yerel |
| API anahtarı ortam değişkeninde yok | yerel |
| `min(güven, kanıt skoru) >= min_local_confidence` | yerel |
| aksi halde | anonim özetle cloud |

Cloud'a giden metin `anonymize.py`'den geçer: gizli değerler silinir, isimler ve adresler
`WORKLOAD_1`, `HOST_1` gibi etiketlere çevrilir ve cevap operatöre gerçek isimlerle gösterilir.
Cloud cevabı kanıtla daha iyi desteklenmiyorsa yerel sonuç korunur; benimsense bile güven kanıt
skorunu geçemez, yani cloud'un sözüyle PR açılmaz. Gönderilen metnin tamamı audit log'da ve
`second_opinion.sent_payload` alanında durur. Ayrıntılar: [SECURITY.md](SECURITY.md).

```bash
export AIOPS_CLOUD_API_KEY=...        # anahtar dosyada değil ortamda
AIOPS_CLOUD_ENABLED=true .venv/bin/python -m eval.run_eval --cloud   # cloud_route_rate, cloud_agreed
```

## Mac'te test ortamı (Apple Silicon, 16 GB)

Docker Desktop'a ~6 GB RAM ver. Ollama, cluster'ın dışında, Mac'te Metal ile çalışır.

```bash
make setup          # kind, kubectl, helm, ollama + model (qwen2.5:7b-instruct) + .venv
make up             # kind cluster + Prometheus/Alertmanager/Grafana + Loki + Alloy (~5-10 dk)
make run            # copilot :8080'de dinler (ayrı terminalde açık kalsın)

make scenario-oom           # checkout-api OOMKilled  → bellek limiti artışı önerir
make scenario-bad-deploy    # payment-gateway yeni imajla çöker → önceki imaja rollback önerir
make scenario-image-pull    # inventory-svc yanlış imaj etiketi → rollback önerir
make scenario-config-error  # notification-svc eksik ConfigMap → manuel öneri
make scenario-reset
```

Alert 1-2 dakikada gelir. Sonuçlar:

- `out/incidents/<id>.json`: tüm analiz (sinyaller, kanıtlar, kök neden, süreler)
- `out/<id>.patch` ve `out/<id>-pr.md`: önerilen diff ve PR metni (dry-run)
- `http://localhost:8080/incidents`: API
- Grafana `http://localhost:3000` (admin/admin), Prometheus `:9090`, Alertmanager `:9093`

**Gerçek PR açmak için:** `config.yaml` içinde `gitops.dry_run: false` yap ve `export GITHUB_TOKEN=...`
(sadece bu repoya `contents:write` + `pull_requests:write` yetkili fine-grained token).
Copilot geçici bir git worktree kullanır; senin çalışma dizinine dokunmaz. Senaryo scriptleri
"deploy"u `gitops/apps` içinde yaptığı için PR modunda önce o değişikliği commit/push et.

> Alertmanager copilot'a `host.docker.internal:8080` üzerinden ulaşır (Docker Desktop). Çalışmazsa
> `kubectl -n monitoring logs alertmanager-kps-kube-prometheus-stack-alertmanager-0` ile kontrol et.

## Model değerlendirmesi (eval)

`eval/fixtures.yaml` içinde 12 kayıtlı incident var. Satışta göstereceğin tablo bu:

```bash
make eval-rules     # sadece kurallar: 10/12 (bilinçli olarak 2 vaka kurallarla çözülemez)
make eval           # kurallar + yerel model: doğruluk ve vaka başına süre
```

Sonuç `out/eval/results-<model>.json` dosyasına yazılır. Farklı modelleri (`AIOPS_LLM_MODEL=...`) ve
CPU-only bir llama.cpp sunucusunu aynı tabloyla karşılaştır. Müşteri ortamında kendi geçmiş
incident'larından fixture eklemek, POC'nin en ikna edici parçası olur.

## Geliştirme

```bash
make test           # pytest (LLM ve GitHub mock'lu, cluster gerekmez)
make lint
.venv/bin/aiops-copilot -c config.yaml analyze alert.json   # tek bir alert'i analiz et
```

## Yapı

| Yol | İçerik |
|---|---|
| `src/aiops_copilot/collectors/` | Kubernetes, Prometheus, Loki, Git (salt okuma) |
| `src/aiops_copilot/preprocess/` | Drain3 log özeti, kural tabanlı sinyaller, bağlam bütçesi |
| `src/aiops_copilot/llm.py` | OpenAI uyumlu yerel model istemcisi ve prompt |
| `src/aiops_copilot/pipeline.py` | Akış, model+kural birleştirme, politika kapısı |
| `src/aiops_copilot/confidence.py` | Kanıta dayalı güven skoru |
| `src/aiops_copilot/routing.py` | Yerel / cloud yönlendirme kararı |
| `src/aiops_copilot/anonymize.py` | Gizli değer silme, geri çevrilebilir maskeleme |
| `src/aiops_copilot/actions/` | GitOps patch'leri ve PR açma |
| `runbooks/` | Onaylı düzeltmeler (model sadece bunlardan seçer) |
| `gitops/apps/` | Demo uygulamaları ve arıza senaryoları |
| `deploy/` | kind ve monitoring stack değerleri |
| `eval/` | Kayıtlı incident'lar ve doğruluk/süre ölçümü |

## Yol haritası

| Hafta | Kapsam | Durum |
|---|---|---|
| 1 | Yerel AI motoru: toplayıcılar, Drain3, kurallar, yerel LLM, runbook, dry-run PR, audit, eval | Tamam |
| 2 | Hibrit AI: kanıt skoru, yerel/cloud yönlendirme, anonimleştirme, cloud kullanım oranı | Bu sürüm |
| 3 | Geri bildirim ve bilgi tabanı: PostgreSQL + pgvector, incident UI (React), doğru/kısmen/yanlış oylama, geçmiş olaylardan RAG, Slack/Teams etkileşimli butonlar | Sırada |
| 4 | Düzeltme: gerçek GitHub PR, ArgoCD takibi ve deploy sonrası doğrulama, Terraform plan + onay, Ansible/AWX, Jira/ServiceNow kaydı | Planlandı |
| ★ | Tempo trace, Elasticsearch/Graylog, Helm chart + RBAC + Dockerfile + CI, 20+ senaryo, demo videosu | Planlandı |

## Katkı ve lisans

Apache License 2.0. Katkı için [CONTRIBUTING.md](CONTRIBUTING.md), güvenlik bildirimi için [SECURITY.md](SECURITY.md).
Kurumsal kurulum, entegrasyon ve destek ayrıca sunulur (Enterprise).
