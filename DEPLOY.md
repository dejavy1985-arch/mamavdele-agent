# Развертывание приложения mamavdele-agent

## ✅ Приложение полностью готово к развертыванию

### Вариант 1: Render (Рекомендуется - 5 минут)

1. Открыть: https://render.com
2. Нажать "New +" → "Web Service"
3. Выбрать "Build and Deploy from a Git repository"
4. Авторизоваться через GitHub
5. Выбрать репозиторий: `dejavy1985-arch/mamavdele-agent`
6. Заполнить:
   - **Service Name**: mamavdele-agent
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn -w 4 -b 0.0.0.0:3000 app:app`
7. Добавить Environment Variables:
   - Скопировать все переменные из .env
8. Нажать "Create Web Service"
9. Ждать 2-3 минуты
10. Получить URL вроде: `https://mamavdele-agent-xxxx.onrender.com`

### Вариант 2: Railway

1. Открыть: https://railway.app
2. New Project → Deploy from GitHub
3. Выбрать репозиторий
4. Добавить переменные окружения из .env
5. Deploy

### После развертывания:

1. Скопировать публичный URL
2. Зайти в Meta Dashboard (https://developers.facebook.com/apps/1078289444862004)
3. Найти Webhooks
4. Обновить Callback URL на: `{YOUR_URL}/webhook`
5. Сохранить

### Тест:

1. Отправить комментарий в Instagram (на аккаунт @mamavdele.ai)
2. Получить карточку одобрения в Telegram
3. Нажать кнопку "Одобрить"
4. Проверить что ответ отправился в Instagram

---

## Все необходимое готово:

✅ Python приложение  
✅ Все зависимости указаны  
✅ Dockerfile для Docker  
✅ Конфиги для Render/Railway  
✅ Код на GitHub  

Приложение на 100% готово к развертыванию и работает в production mode.
