# Güvenlik politikası

## Açık bildirimi

Güvenlik açıklarını lütfen herkese açık issue olarak **açmayın**. GitHub'daki
**Security → Report a vulnerability** (private vulnerability reporting) ile bildirin.
İlk yanıt hedefi 3 iş günüdür.

## Kapsam ve tasarım güvenceleri

- Copilot varsayılan olarak **salt okuma** çalışır; hiçbir komutu kendisi çalıştırmaz, PR'ları merge etmez.
- Cloud ikinci görüş **varsayılan olarak kapalıdır**. Açıldığında yalnızca anonimleştirilmiş özet gönderilir:
  parola, token, JWT, private key gibi değerler silinir; namespace, workload, pod, host, IP ve e-posta
  geri çevrilebilir etiketlerle (`NAMESPACE_1`...) değiştirilir. Gönderilen metnin tamamı audit log'a ve
  incident kaydına (`second_opinion.sent_payload`) yazılır.
- `local_only_namespaces` ve `data_classification` etiketi ile işaretlenen olaylar hiçbir koşulda dışarı çıkmaz.
- Her adım hash zincirli `audit.jsonl` dosyasına yazılır; `aiops-copilot audit-verify` ile doğrulanır.
- API anahtarları yapılandırma dosyasına değil ortam değişkenine konur.

Anonimleştirme regex tabanlıdır ve %100 garanti vermez. Düzenlemeye tabi ortamlarda (ör. BDDK) cloud
özelliğini açmadan önce `sent_payload` örneklerini kendi verinizle inceleyin.
