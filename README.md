# AIOps Copilot (MVP)

Kurum içinde çalışan, **salt okuma** bir incident copilot. Alertmanager'dan gelen alert'i alır;
Kubernetes, Prometheus, Loki ve Git'ten bağlamı toplar; kök nedeni **yerel bir LLM** ile çıkarır;
düzeltmeyi GitOps reposuna **pull request** olarak önerir. Hiçbir şeyi kendisi çalıştırmaz ve merge etmez.

```
Alertmanager ──webhook──▶ Copilot ──▶ Kubernetes / Prometheus / Loki / Git   (salt okuma)
                              │
                              ├─ Drain3 log şablonları + kural tabanlı sinyaller (CPU, ms)
                              ├─ Yerel LLM (Ollama / llama.cpp / vLLM, OpenAI uyumlu API)
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
| `src/aiops_copilot/actions/` | GitOps patch'leri ve PR açma |
| `runbooks/` | Onaylı düzeltmeler (model sadece bunlardan seçer) |
| `gitops/apps/` | Demo uygulamaları ve arıza senaryoları |
| `deploy/` | kind ve monitoring stack değerleri |
| `eval/` | Kayıtlı incident'lar ve doğruluk/süre ölçümü |

## Sıradaki adımlar

- Graylog ve Elasticsearch collector'ları
- ArgoCD entegrasyonu (PR merge → otomatik sync, rollout durumu)
- Teams adaptive card ve ITSM (Jira/ServiceNow) kaydı
- Geçmiş postmortem'ler üzerinde arama (runbook önerisini güçlendirmek için)
- Air-gapped kurulum: Helm chart + offline imajlar + llama.cpp CPU imajı
