# Local vTuner

[English](#internet-radio-for-old-pioneer-receivers) | [Українська](#інтернет-радіо-для-старих-ресиверів-pioneer)

## Internet radio for old Pioneer receivers

The add-on replaces the discontinued vTuner service. The receiver (VSX-527 and similar) still opens **Internet Radio**, but takes the menu and the stations from your Home Assistant.

What it does:

- **My Stations** — your own station list from the add-on options, with folders.
- **Radio Browser** — the radio-browser.info catalogue: your country, popular, countries, genres.
- Relays **HTTPS streams and .m3u/.pls playlists** as plain HTTP: an old receiver cannot play them by itself.
- Built-in **DNS** that answers `pioneer.vtuner.com` with the address of Home Assistant.

### Installation

1. **Settings → Apps → Install app → ⋮ → Repositories** (in older Home Assistant versions the section is called "Add-ons"). Paste `https://github.com/alx2080/vTuner_Local` → **Add**.
2. Find **Local vTuner** in the store → **Install**. New versions appear as ordinary updates.
3. On the **Configuration** tab add your stations, then press **Start**.
4. Open the add-on's web interface (`http://Home-Assistant-IP/`): it shows that the server is running.

Port **80** on Home Assistant must be free (the Home Assistant interface on 8123 does not interfere).

### Pointing the receiver at the add-on

**Option A — the built-in DNS (the easiest).** On the receiver: **HOME MENU → Network Setup → IP Address/Proxy** → DHCP **OFF**, set the address manually and enter the IP of your Home Assistant in the **DNS** field. The rest of the internet keeps working: other queries are forwarded to the upstream DNS.

**Option B — your router, AdGuard Home or Pi-hole.** Create the DNS record `pioneer.vtuner.com → Home Assistant IP` and turn off "Built-in DNS" in the add-on (otherwise it takes port 53, which AdGuard needs).

Then **unplug the receiver from the mains for a minute** (it caches addresses), switch it on and choose the input **NETWORK → Internet Radio**.

### Stations

```yaml
- name: Radio Rocks
  url: https://example.com/stream.mp3
  folder: Rock
- name: News
  url: http://example.com/news.m3u
  folder: ""
```

MP3 and AAC streams are supported. The receiver cannot play HLS streams (`.m3u8`).

If the receiver's display shows boxes instead of Cyrillic letters, turn on **Transliteration**.

### Troubleshooting

Open the **Log** tab of the add-on and browse the receiver's menu. Every request of the receiver is visible in the log. If there are no requests, the receiver did not reach the add-on (a DNS problem). If there are lines with `unhandled vTuner request`, send them to [Issues](https://github.com/alx2080/vTuner_Local/issues) so that support can be added.

---

## Інтернет-радіо для старих ресиверів Pioneer

Додаток замінює закритий сервіс vTuner. Ресивер (VSX-527 і подібні) так само відкриває пункт **Internet Radio**, але меню й станції бере з вашого Home Assistant.

Що вміє:

- **My Stations** — ваш список станцій із налаштувань додатка, з теками.
- **Radio Browser** — каталог radio-browser.info: ваша країна, популярні, країни, жанри.
- Ретранслює **HTTPS-потоки та плейлисти .m3u/.pls** як звичайний HTTP: старий ресивер сам їх не відтворює.
- Вбудований **DNS**, який відповідає на `pioneer.vtuner.com` адресою Home Assistant.

### Встановлення

1. **Налаштування → Застосунки → Встановити додаток → ⋮ → Репозиторії** (у старих версіях Home Assistant розділ називається «Додатки»). Вставте `https://github.com/alx2080/vTuner_Local` → **Додати**.
2. Знайдіть **Local vTuner** у магазині → **Встановити**. Нові версії з'являться як звичайне оновлення.
3. На вкладці **Конфігурація** додайте станції й запустіть додаток.
4. Відкрийте вебінтерфейс додатка (`http://IP-Home-Assistant/`) — там видно, що сервер працює.

Порт **80** на Home Assistant має бути вільним (інтерфейс HA на 8123 не заважає).

### Як спрямувати ресивер на додаток

**Варіант А — вбудований DNS (найпростіше).** На ресивері: **HOME MENU → Network Setup → IP Address/Proxy** → DHCP **OFF**, задайте адресу вручну, а в полі **DNS** вкажіть IP вашого Home Assistant. Решта інтернету працюватиме й далі: інші запити пересилаються на зовнішній DNS.

**Варіант Б — через роутер, AdGuard Home або Pi-hole.** Створіть DNS-запис `pioneer.vtuner.com → IP Home Assistant` і вимкніть у додатку «Вбудований DNS» (інакше він займе порт 53, потрібний AdGuard).

Після цього **вийміть вилку ресивера з розетки на хвилину** (він кешує адреси), увімкніть і виберіть вхід **NETWORK → Internet Radio**.

### Станції

```yaml
- name: Радіо Рокс
  url: https://example.com/stream.mp3
  folder: Рок
- name: Новини
  url: http://example.com/news.m3u
  folder: ""
```

Підтримуються потоки MP3 і AAC. Потоки HLS (`.m3u8`) ресивер відтворювати не вміє.

Якщо дисплей ресивера показує квадратики замість кирилиці, увімкніть **Транслітерацію**.

### Якщо не працює

Відкрийте вкладку **Журнал** додатка й погортайте меню ресивера. Кожен запит ресивера видно в журналі. Якщо запитів немає — ресивер не дістався до додатка (проблема DNS). Якщо є рядки `unhandled vTuner request` — надішліть їх в [Issues](https://github.com/alx2080/vTuner_Local/issues), щоб додати підтримку.
