# Деплой на свій сервер (VPS)

Сайт: **https://fire.metalgearbot.fun**. Сервер спільний з іншими проєктами власника, тому:
- назовні порт не відкриваємо — сайт віддає спільний Caddy (`metal-gear-caddy`), він сам робить HTTPS;
- контейнер `firecal` у docker-мережі `web`, ліміт пам'яті 1 ГБ (`docker-compose.yml`);
- блок сайту — у `Caddyfile` репо **metal_gear_bot** (не тут!), там же правила, як його оновлювати.

Даних окремо качати не треба: `data/deploy/ukraine.parquet` лежить у git.

## Перший запуск
```bash
cd /root
git clone https://github.com/Toksa88/Hakaton.git firecal
cd firecal
docker compose up -d --build          # збирає образ (~кілька хвилин) і запускає
docker ps --filter name=firecal        # STATUS має стати "healthy" за ~1 хв
```
Потім у репо metal_gear_bot має бути блок `fire.metalgearbot.fun` у `Caddyfile`, і Caddy
перестворюється командою з шапки того `Caddyfile` (НЕ `caddy reload`).

## Оновити після злиття PR
```bash
cd /root/firecal && git pull && docker compose up -d --build
```
Сайт перезапускається за кілька секунд; відкриті вкладки самі перепідключаться.

## Куди потрапляють файли, які завантажують користувачі
«⚙️ Додатково» → «Свої дані FIRMS»: файл їде по HTTPS у контейнер `firecal` і живе **лише в пам'яті**
процесу (`get_points` у `st.cache_resource`, не більше 3 наборів — старші витісняються). На диск,
у базу, у логи чи в git нічого не пишеться; після `docker compose up -d --build` чи рестарту — зникає.
Інший відвідувач його не бачить (ключ кешу — сам вміст файлу). Ліміт — 30 МБ на файл
(`.streamlit/config.toml`, `maxUploadSize`): контейнер обмежений 1 ГБ пам'яті.

## Якщо щось не так
- `docker logs --tail 50 firecal` — помилки Python;
- `docker stats --no-stream firecal` — пам'ять (норма — кілька сотень МБ);
- 502 / сторінка техробіт — контейнер не запущений або не в мережі `web`.
