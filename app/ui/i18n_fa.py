"""Persian texts of the Simple view (spec A, F0): English source text, then Persian.

A source text with `{name}` fields is a template; each field's value is translated again, so a
symbol name inside a sentence is Persian too. Numbers, prices and symbols stay as they are.
"""

from __future__ import annotations

PAIRS: tuple[tuple[str, str], ...] = (
    # First start
    ("Welcome", "خوش آمدید"),
    ("This is practice money, not real", "این پول تمرینی است، نه واقعی"),
    (
        "The app starts in practice mode. It watches the market for you and suggests trades. When "
        "you approve one, it is only simulated on live prices: no real money is used, and nothing "
        "reaches your account until you choose otherwise.",
        "برنامه در حالت تمرینی شروع می‌شود. بازار را برای شما زیر نظر می‌گیرد و معامله پیشنهاد "
        "می‌دهد. وقتی یکی را تأیید کنید، فقط روی قیمت‌های زنده شبیه‌سازی می‌شود: هیچ پول واقعی "
        "خرج نمی‌شود و تا خودتان نخواهید چیزی به حسابتان نمی‌رسد.",
    ),
    ("How much do you know about trading?", "چقدر با معامله‌گری آشنا هستید؟"),
    ("I'm new to trading: keep it simple", "تازه‌کارم: ساده نگهش دار"),
    ("I already trade: show the Advanced view", "معامله‌گرم: نمای پیشرفته را نشان بده"),
    # Home
    ("Home", "خانه"),
    (
        "Practice money: trades are simulated, nothing real is bought or sold.",
        "پول تمرینی: معامله‌ها شبیه‌سازی می‌شوند و هیچ چیز واقعی خرید و فروش نمی‌شود.",
    ),
    (
        "Real orders: a trade is placed only after you approve it.",
        "سفارش واقعی: هر معامله فقط بعد از تأیید شما ثبت می‌شود.",
    ),
    (
        "Watching only: the app shows suggestions but places nothing.",
        "فقط تماشا: برنامه پیشنهاد نشان می‌دهد ولی هیچ سفارشی ثبت نمی‌کند.",
    ),
    ("Automatic trading is not available yet.", "معامله خودکار هنوز در دسترس نیست."),
    ("BALANCE", "موجودی"),
    ("PRACTICE BALANCE", "موجودی تمرینی"),
    ("Balance", "موجودی"),
    ("not known yet", "هنوز معلوم نیست"),
    ("Today: not known yet", "امروز: هنوز معلوم نیست"),
    ("Today: {result}", "امروز: {result}"),
    ("Last 7 days: {result}", "۷ روز گذشته: {result}"),
    # The suggestion card
    ("{position} of {total} suggestions", "پیشنهاد {position} از {total}"),
    ("{name} ({symbol})", "{name} ({symbol})"),
    ("Buy", "خرید"),
    ("Sell", "فروش"),
    ("▲ Buy", "▲ خرید"),
    ("▼ Sell", "▼ فروش"),
    ("The {name} rate", "نرخ {name}"),
    (
        "{who} is trending up and just dipped back, so it may rise again.",
        "روند {who} صعودی است و تازه کمی عقب نشسته، پس شاید دوباره بالا برود.",
    ),
    (
        "{who} is trending down and just bounced back, so it may fall again.",
        "روند {who} نزولی است و تازه کمی برگشته، پس شاید دوباره پایین بیاید.",
    ),
    (
        "{who} stayed in a narrow range overnight. If it breaks above that range after the London "
        "market opens, the move may carry on.",
        "{who} در طول شب در محدوده‌ای باریک ماند. اگر بعد از باز شدن بازار لندن از بالای این "
        "محدوده بیرون بزند، شاید حرکت ادامه پیدا کند.",
    ),
    (
        "{who} stayed in a narrow range overnight. If it breaks below that range after the London "
        "market opens, the move may carry on.",
        "{who} در طول شب در محدوده‌ای باریک ماند. اگر بعد از باز شدن بازار لندن از پایین این "
        "محدوده بیرون بزند، شاید حرکت ادامه پیدا کند.",
    ),
    ("The app found a chance that {who} may rise.", "برنامه احتمال می‌دهد {who} بالا برود."),
    ("The app found a chance that {who} may fall.", "برنامه احتمال می‌دهد {who} پایین بیاید."),
    (
        "You could lose about {amount} if it goes wrong",
        "اگر اشتباه پیش برود، حدود {amount} ضرر می‌کنید",
    ),
    (
        "You could make about {amount} if it goes right",
        "اگر درست پیش برود، حدود {amount} سود می‌کنید",
    ),
    (
        "You could lose: not known until the account is read",
        "ضرر احتمالی: تا خوانده شدن حساب معلوم نیست",
    ),
    (
        "You could make: not known until the account is read",
        "سود احتمالی: تا خوانده شدن حساب معلوم نیست",
    ),
    ("Confidence: not known yet", "اطمینان: هنوز معلوم نیست"),
    (
        "The app needs {count} finished suggestions of this kind before it can say.",
        "برنامه قبل از نظر دادن به {count} پیشنهاد تمام‌شده از این نوع نیاز دارد.",
    ),
    ("Fairly confident", "نسبتاً مطمئن"),
    ("Moderately confident", "تا حدی مطمئن"),
    ("Low confidence", "اطمینان کم"),
    (
        "Based on {count} earlier suggestions of this kind.",
        "بر اساس {count} پیشنهاد قبلی از این نوع.",
    ),
    ("This suggestion has ended", "مهلت این پیشنهاد تمام شد"),
    ("This suggestion ends in {time}", "این پیشنهاد {time} دیگر تمام می‌شود"),
    ("{hours} h {minutes} min", "{hours} ساعت و {minutes} دقیقه"),
    ("{minutes} min", "{minutes} دقیقه"),
    (
        "The price has moved since this was found. It is checked again when you approve and may "
        "be cancelled.",
        "قیمت از وقتی این پیشنهاد پیدا شد جابه‌جا شده است. هنگام تأیید دوباره بررسی می‌شود و ممکن "
        "است لغو شود.",
    ),
    (
        "Watching only: choose practice or real orders in the Advanced view to approve.",
        "فقط تماشا: برای تأیید، در نمای پیشرفته حالت تمرینی یا سفارش واقعی را انتخاب کنید.",
    ),
    ("Approve is not available right now: {reason}.", "تأیید الان ممکن نیست: {reason}."),
    (
        "Approve is not available: the signals are not running.",
        "تأیید ممکن نیست: بخش پیشنهادها در حال اجرا نیست.",
    ),
    ("Approve", "تأیید"),
    ("Skip", "رد کردن"),
    ("Show details", "نمایش جزئیات"),
    ("Hide details", "پنهان کردن جزئیات"),
    ("No trade suggestions right now", "فعلاً پیشنهاد معامله‌ای نیست"),
    (
        "The app tells you here as soon as it finds one. Many hours have no good trade, and that "
        "is normal.",
        "به محض پیدا کردن یکی، همین‌جا خبرتان می‌کند. خیلی از ساعت‌ها معامله خوبی ندارند و این "
        "طبیعی است.",
    ),
    (
        "Connect MetaTrader 5 in Settings (top right) so the app can watch the market.",
        "متاتریدر ۵ را از تنظیمات (بالای صفحه) وصل کنید تا برنامه بتواند بازار را زیر نظر بگیرد.",
    ),
    # Details (shown after Show details)
    ("Chance it works: {text}", "احتمال موفقیت: {text}"),
    (
        "{value}% (likely between {low}% and {high}%), from {count} past results",
        "{value}% (احتمالاً بین {low}% و {high}%)، از {count} نتیجه قبلی",
    ),
    (
        "not known yet: {count} of {needed} past results",
        "هنوز معلوم نیست: {count} از {needed} نتیجه قبلی",
    ),
    ("Order: {text}", "سفارش: {text}"),
    ("{action} now at about {price}", "{action} همین حالا با قیمت حدود {price}"),
    ("{action} when the price reaches {price}", "{action} وقتی قیمت به {price} برسد"),
    ("{action} if the price comes back to {price}", "{action} اگر قیمت به {price} برگردد"),
    ("Exit if it goes wrong (stop loss): {price}", "خروج اگر اشتباه پیش برود (حد ضرر): {price}"),
    ("Exit if it goes right (take profit): {price}", "خروج اگر درست پیش برود (حد سود): {price}"),
    ("Size: {text}", "حجم: {text}"),
    (
        "{size}, sized from your account and risk settings",
        "{size}، بر اساس حساب و تنظیمات ریسک شما",
    ),
    ("{volume} lots", "{volume} لات"),
    ("Expected result: {text}", "نتیجه مورد انتظار: {text}"),
    (
        "{times} times the amount at risk, on average (EV {ev} R)",
        "به طور میانگین {times} برابر مبلغ در معرض ریسک (EV {ev} R)",
    ),
    ("Chart: {text}", "نمودار: {text}"),
    ("{count} minute", "{count} دقیقه"),
    ("{count} minutes", "{count} دقیقه"),
    ("{count} hour", "{count} ساعت"),
    ("{count} hours", "{count} ساعت"),
    ("{count} day", "{count} روز"),
    ("Strategy: {text}", "استراتژی: {text}"),
    ("{name} (an example, not proven to pay)", "{name} (یک نمونه، سودآوری‌اش ثابت نشده)"),
    ("Why, in detail: {text}", "دلیل، با جزئیات: {text}"),
    # Open trades
    ("Your open trades", "معامله‌های باز شما"),
    ("No open trades.", "معامله بازی ندارید."),
    ("Close now", "همین حالا ببند"),
    ("Waiting for its price", "منتظر رسیدن قیمت"),
    ("Result not known yet", "نتیجه هنوز معلوم نیست"),
    # Bottom of the screen
    ("Stop trading now", "توقف معامله‌ها"),
    (
        "Closes the app's trades, cancels its orders and stops new ones (asks first).",
        "معامله‌های برنامه را می‌بندد، سفارش‌هایش را لغو می‌کند و جلوی معامله جدید را می‌گیرد "
        "(اول می‌پرسد).",
    ),
    ("Nothing is running yet.", "هنوز چیزی در حال اجرا نیست."),
    ("Show status bar", "نمایش نوار وضعیت"),
    ("Hide status bar", "پنهان کردن نوار وضعیت"),
    (
        "Not connected to MetaTrader 5, so the app is not watching the market.",
        "به متاتریدر ۵ وصل نیست، پس برنامه بازار را زیر نظر ندارد.",
    ),
    (
        "Stopped: you pressed Stop trading. Nothing new is opened until you allow it.",
        "متوقف: دکمه توقف معامله‌ها را زدید. تا اجازه ندهید معامله جدیدی باز نمی‌شود.",
    ),
    (
        "Paused: today's loss limit is reached. New trades are allowed again tomorrow.",
        "مکث: سقف ضرر امروز پر شده است. از فردا دوباره معامله جدید مجاز است.",
    ),
    (
        "Paused: the account fell too far below its best level. Allow trading again in the "
        "Advanced view (Risk).",
        "مکث: حساب بیش از حد از بهترین سطحش پایین آمده است. در نمای پیشرفته (ریسک) دوباره اجازه "
        "معامله بدهید.",
    ),
    (
        "Paused: new trades are stopped. Allow them again in the Advanced view (Risk).",
        "مکث: معامله جدید متوقف است. در نمای پیشرفته (ریسک) دوباره اجازه بدهید.",
    ),
    ("Found a trade for you.", "یک معامله برایتان پیدا شد."),
    ("Found {count} trades.", "{count} معامله پیدا شد."),
    ("Trade running.", "یک معامله در جریان است."),
    ("{count} trades running.", "{count} معامله در جریان است."),
    (
        "The market is closed. The app starts watching again when it opens.",
        "بازار بسته است. برنامه وقتی بازار باز شود دوباره زیر نظرش می‌گیرد.",
    ),
    ("Watching the market…", "در حال زیر نظر گرفتن بازار…"),
    (
        "Approved: it is checked again and placed in a few seconds.",
        "تأیید شد: دوباره بررسی می‌شود و تا چند ثانیه دیگر ثبت می‌شود.",
    ),
    ("Closing: done in a few seconds.", "در حال بستن: تا چند ثانیه دیگر انجام می‌شود."),
    ("Status: not connected", "وضعیت: وصل نیست"),
    (
        "Status: connected to {company}, {kind} account {login}.",
        "وضعیت: به {company} وصل است، حساب {kind} {login}.",
    ),
    (
        "Status: connected to {company}, {kind} account {login}. Read-only login, so the app only "
        "watches and never trades.",
        "وضعیت: به {company} وصل است، حساب {kind} {login}. ورود فقط‌خواندنی است، پس برنامه فقط "
        "تماشا می‌کند و هیچ‌وقت معامله نمی‌کند.",
    ),
    ("practice (demo)", "تمرینی (دمو)"),
    ("contest", "مسابقه"),
    ("REAL money", "پول واقعی"),
    ("trading", "معاملاتی"),
    (
        "Status: the connection to MetaTrader 5 was lost. Reconnecting...",
        "وضعیت: اتصال به متاتریدر ۵ قطع شد. در حال اتصال دوباره...",
    ),
    ("Status: connecting to MetaTrader 5...", "وضعیت: در حال اتصال به متاتریدر ۵..."),
    (
        "Status: could not connect to MetaTrader 5. Advanced view > Settings shows why.",
        "وضعیت: اتصال به متاتریدر ۵ برقرار نشد. علتش در نمای پیشرفته > تنظیمات آمده است.",
    ),
    # Questions
    ("Approve this trade?", "این معامله تأیید شود؟"),
    ("Close this trade?", "این معامله بسته شود؟"),
    ("{action} {title}?", "{action} {title}؟"),
    (
        "This places a REAL order on your MetaTrader 5 account.",
        "این کار یک سفارش واقعی روی حساب متاتریدر ۵ شما ثبت می‌کند.",
    ),
    (
        "This is practice money: the trade is only simulated.",
        "این پول تمرینی است: معامله فقط شبیه‌سازی می‌شود.",
    ),
    (
        "The price is checked again before anything is placed.",
        "قبل از ثبت هر چیزی، قیمت دوباره بررسی می‌شود.",
    ),
    ("Close {title} now at the current price?", "{title} همین حالا با قیمت فعلی بسته شود؟"),
    (
        "Its result now: {result}. The final result can differ a little.",
        "نتیجه فعلی: {result}. نتیجه نهایی ممکن است کمی فرق کند.",
    ),
    # Top bar
    ("MT5 Trading Workstation", "میز معامله MT5"),
    ("Settings", "تنظیمات"),
    ("Back to Home", "بازگشت به خانه"),
    ("Switch to Advanced", "نمای پیشرفته"),
    ("Switch to Simple", "نمای ساده"),
    ("Switch to the light theme", "تم روشن"),
    ("Switch to the dark theme", "تم تیره"),
    ("Light theme", "تم روشن"),
    ("Dark theme", "تم تیره"),
    ("MT5 not connected", "متاتریدر وصل نیست"),
    ("MT5 connecting", "در حال اتصال"),
    ("MT5 connected", "متاتریدر وصل است"),
    ("MT5 failed", "اتصال ناموفق"),
    ("Demo · {login}", "دمو · {login}"),
    ("REAL · {login}", "واقعی · {login}"),
    ("Operating mode", "حالت کار"),
    ("PAPER", "تمرینی"),
    ("SEMI-AUTO", "نیمه‌خودکار"),
    ("AUTO", "خودکار"),
    ("ANALYSIS-ONLY", "فقط تحلیل"),
    # Plain names of symbols
    ("Euro vs US dollar", "یورو به دلار آمریکا"),
    ("British pound vs US dollar", "پوند انگلیس به دلار آمریکا"),
    ("US dollar vs Japanese yen", "دلار آمریکا به ین ژاپن"),
    ("US dollar vs Swiss franc", "دلار آمریکا به فرانک سوئیس"),
    ("US dollar vs Canadian dollar", "دلار آمریکا به دلار کانادا"),
    ("Australian dollar vs US dollar", "دلار استرالیا به دلار آمریکا"),
    ("New Zealand dollar vs US dollar", "دلار نیوزیلند به دلار آمریکا"),
    ("Euro vs British pound", "یورو به پوند انگلیس"),
    ("Euro vs Japanese yen", "یورو به ین ژاپن"),
    ("British pound vs Japanese yen", "پوند انگلیس به ین ژاپن"),
    ("Euro vs Swiss franc", "یورو به فرانک سوئیس"),
    ("Australian dollar vs Japanese yen", "دلار استرالیا به ین ژاپن"),
    ("Gold", "طلا"),
    ("Silver", "نقره"),
    ("Bitcoin", "بیت‌کوین"),
    ("Ether", "اتریوم"),
)
PERSIAN: dict[str, str] = dict(PAIRS)
