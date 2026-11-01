# Laptop fiyat takibi (amazon.com.tr)

Her saat amazon.com.tr’yi tarar, filtreleri geçen oyun laptoplarının fiyatını kaydeder, sonuçları GitHub Pages sitesinde gösterir, takip edilen en düşük fiyatın %10 altına inen ürün için Telegram bildirimi gönderir.

## Kurulum

1. GitHub’da `laptop-fiyat-takip` adında **public** bir repo oluştur (README eklemeden).
2. Bu klasörün tüm içeriğini repoya yükle. `.github` klasörü gizli olduğu için web arayüzünden sürükle-bırakta görünmeyebilir; yükledikten sonra repoda `.github/workflows/tarama.yml` dosyasının olduğunu kontrol et. Yoksa GitHub’da **Add file → Create new file** ile bu yolu yazıp dosyanın içeriğini yapıştır.
3. **Settings → Secrets and variables → Actions → New repository secret** ile iki secret ekle:
   - `TELEGRAM_BOT_TOKEN`: @BotFather’dan aldığın token
   - `TELEGRAM_CHAT_ID`: botuna mesaj attıktan sonra `https://api.telegram.org/bot<TOKEN>/getUpdates` adresinde görünen `chat.id`
4. **Settings → Pages → Build and deployment → Source** alanını **GitHub Actions** yap.
5. **Actions** sekmesinde iş akışlarını etkinleştir, **Tarama → Run workflow** ile ilk taramayı başlat.

Site adresi: `https://unbug-bot.github.io/laptop-fiyat-takip/`

## Filtreler

`config.yaml` içinde. GPU ve işlemci için beyaz liste kullanılır; listede olmayan ama kesin olarak düşük olduğu bilinen modeller (ör. RTX 4070, Core i7, Ryzen 7) reddedilir, tanınmayan yeni modeller onaya düşer.

Her özellik üç kaynaktan okunur: ürün başlığı, teknik ayrıntılar tablosu, ürün açıklaması maddeleri.

- Tüm bulunan değerler filtreyi geçiyorsa özellik geçer.
- Tümü geçmiyorsa ürün reddedilir.
- Değer bulunamazsa, değerler çelişiyorsa ya da model tanınmıyorsa ürün **onay bekleyenler** listesine düşer.

Onay bekleyen ürünler bildirim almaz, fiyatları takip edilir. Karar için ASIN’i `overrides.yaml` dosyasına ekle; dosyayı kaydetmek taramayı hemen tetikler. Otomatik olarak reddedilmiş bir ürün (kesin filtre ihlali) elle onaylanamaz.

## Fiyat ve bildirim kuralları

- Yalnızca **Yeni** durumdaki teklifler sayılır. Kullanılmış, yenilenmiş ve durumu okunamayan teklifler hiçbir hesaba girmez. Başlığında “yenilenmiş”, “ikinci el” vb. geçen ürünler reddedilir.
- Fiyat, tüm satıcılar arasındaki en düşük yeni teklif fiyatıdır. Kargo ücreti dahil edilmez.
- Bildirim: yeni fiyat ≤ takip süresindeki en düşük fiyat × 0,90. Bildirimden sonra yeni fiyat, yeni en düşük fiyat olur.
- Hiç fiyatı görülmemiş ürünün ilk fiyatı referanstır, bildirim gelmez. Stoktan çıkıp dönen ürün aynı kuralla değerlendirilir.
- 120.000 TL üstündeki ürün sitede görünmez ve bildirim almaz; fiyat geçmişi tutulmaya devam eder.

## Bilinen sınırlar

- Amazon, GitHub sunucularından gelen istekleri engelleyebilir. Bu durumda sitede durum satırı “Amazon istekleri engelledi” yazar ve o turda hiçbir fiyat değiştirilmez.
- Sayfa yapısı değişirse ayrıştırılamayan sayfalar iş akışı çalıştırmasının **Artifacts** bölümünde `debug-html` olarak 3 gün saklanır.
- Tüm takip edilen ürünler aynı turda stok dışı görünürse bu bir ayrıştırma hatası sayılır ve o turun fiyatları yok sayılır.
