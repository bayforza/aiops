# Katkı rehberi

AIOps Copilot, Apache-2.0 lisanslı açık kaynak bir projedir (Community Edition). Katkılar memnuniyetle karşılanır.

## Başlarken

```bash
make setup     # .venv + bağımlılıklar
make test      # pytest; LLM, cloud ve GitHub mock'lu, cluster gerekmez
make lint      # ruff
make eval-rules
```

## En değerli katkılar

1. **Yeni incident senaryosu.** `eval/fixtures.yaml` içine anonimleştirilmiş gerçek bir vaka ekleyin
   (alert + kubernetes durumu + log satırları + beklenen kategori). Ürünün kalitesi bu setle ölçülür.
2. **Runbook.** `runbooks/` altına onaylı bir düzeltme. Model yalnızca buradan seçer, fix uydurmaz.
3. **Collector.** Yeni bir veri kaynağı (Elasticsearch, Graylog, Tempo...). `collectors/base.py`
   arayüzünü uygulayın; collector'lar **salt okuma** olmalı.

## Kurallar

- Her değişiklik test ile gelir; `make test` ve `make lint` temiz olmalı.
- Kural tabanlı sinyaller modele tercih edilir: kesin bir sinyal kodla bulunabiliyorsa prompt'a yazmayın.
- Dışarı veri gönderen her yol varsayılan olarak **kapalı** olmalı, `anonymize.py`'den geçmeli ve audit log'a yazılmalı.
- Gerçek müşteri verisi, kimlik bilgisi veya iç hostname içeren fixture göndermeyin.
- Commit mesajları kısa ve açıklayıcı olsun; PR açıklamasında neyi nasıl test ettiğinizi yazın.

## Lisans

Gönderdiğiniz katkılar Apache License 2.0 altında lisanslanır (lisansın 5. maddesi).
