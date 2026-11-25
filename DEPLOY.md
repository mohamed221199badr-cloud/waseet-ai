# نشر Waseet AI مجانًا (رابط ثابت)

التشغيل على 3 خدمات مجانية، من غير كارت ائتمان:

| الجزء | الخدمة | ليه |
| --- | --- | --- |
| قاعدة البيانات PostgreSQL | **Neon** | الخطة المجانية كفاية جدًا للبداية |
| الـ API (FastAPI) | **Render** | خدمة ويب مجانية |
| الشاشة (Streamlit) | **Streamlit Community Cloud** | استضافة مجانية لتطبيقات Streamlit |

## حدود الخطط المجانية (مهم تعرفها)

- **Render:** الخدمة بتنام بعد 15 دقيقة من غير استخدام، وأول طلب بعدها بياخد حوالي دقيقة عشان تصحى (بتظهر صفحة "بتجهز"). في 750 ساعة تشغيل مجانية في الشهر، وهي كفاية لأن الساعات بتتحسب بس وهي صاحية.
- **Neon:** لحد 1 جيجا تخزين لكل مشروع، وقاعدة البيانات بتقفل نفسها بعد 5 دقائق خمول وترجع بسرعة عند أول اتصال.
- **Streamlit Cloud:** التطبيق بينام بعد 12 ساعة من غير زيارات، وأي حد عنده صلاحية يفتحه بضغطة "Yes, get this app back up!". الحساب المجاني فيه **تطبيق خاص (private) واحد** وتطبيقات عامة غير محدودة. طالما الريبو private التطبيق هيطلع private، وده تمام لأن ده التطبيق الوحيد.

## الخطوة 1: قاعدة البيانات على Neon

1. افتح https://neon.tech واعمل حساب (ممكن تسجل بحساب GitHub بتاعك).
2. اعمل مشروع جديد (Create Project) واختار أقرب منطقة.
3. Neon هيديك **Connection String** شكله كده:
   `postgresql://user:password@ep-xxxx.neon.tech/dbname?sslmode=require`
4. انسخه وخليه معاك. (ماتبعتوش لحد غيري.)

## الخطوة 2: الـ API على Render

1. افتح https://render.com واعمل حساب بـ **Sign up with GitHub**.
2. من لوحة التحكم: **New → Blueprint**، واختار ريبو `waseet-ai` (Render هيقرأ ملف `render.yaml` اللي في الريبو وهيجهز كل حاجة).
3. لما يطلب `DATABASE_URL`، الصق رابط Neon اللي نسخته.
4. `JWT_SECRET` هيتولّد لوحده (generateValue في الـ Blueprint). `APP_ENV=prod` متظبطة.
5. بعد ما يخلص deploy، هتلاقي رابط الخدمة شكله: `https://waseet-api.onrender.com`
   - اتأكد إنه شغال: افتح `https://waseet-api.onrender.com/health` ولازم يطلع `{"status":"ok","db":"ok"}`.
6. لو عاوز البيانات التجريبية: غيّر `SEED_DEMO` لـ `"1"` من تبويب Environment واعمل **Manual Deploy → Deploy latest commit**. بعدها ارجع `"0"` عادي.

> ملحوظة: أول فتحة بعد ما ينام ممكن تاخد دقيقة.

## الخطوة 3: الشاشة على Streamlit Community Cloud

1. افتح https://share.streamlit.io وسجل دخول بـ **GitHub**.
2. **Create app → Deploy from GitHub** واختار:
   - Repository: `<اسمك>/waseet-ai`
   - Branch: `main`
   - Main file path: `ui/app.py`
3. قبل ما تدوس Deploy، افتح **Advanced settings → Secrets** وحط السطر ده (بدّل الرابط برابط Render بتاعك):

   ```toml
   WASEET_API_URL = "https://waseet-api.onrender.com/api/v1"
   ```

4. دوس **Deploy**. بعد دقايق هتلاقي رابط الشاشة جاهز، شكله:
   `https://waseet-ai-XXXX.streamlit.app`

## الخطوة 4: حساب المالك بتاعك

البيانات التجريبية فيها حسابين جاهزين (`owner@synthetic-foods.test` / `demo-pass-123` ونفس الباسورد لـ `owner@synthetic-plastics.test`)، لكن ده للتجربة بس.

عشان حسابك الحقيقي بإيميلك وباسورد من اختيارك: قولّي الإيميل والباسورد اللي عاوزهم (أو ابعتهم من هنا وأنا أسجلهم في الخزنة) وأنا أعملهولك على قاعدة البيانات مباشرة، من غير ما تشغل أي حاجة على جهازك.

## تليجرام (اختياري)

ربط عملاء تليجرام من الشاشة شغال من غير أي إعداد. لو عاوز البوت نفسه يستقبل الأوردرات: اعمل بوت من @BotFather، خد التوكن، وحطه في Render كمتغير `TELEGRAM_BOT_TOKEN` (من تبويب Environment). **ماتبعتش التوكن في أي شات عام ولا ترفعه على GitHub.**

## تحديثات مستقبلية

أي تعديل بيتدفع على فرع `main` في GitHub بيعمل deploy لوحده في Render وStreamlit. مفيش أي خطوة يدوية.
