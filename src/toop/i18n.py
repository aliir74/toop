"""Bilingual (Persian/English) string catalog and lookup for توپ.

Every user-facing string the bot emits lives here behind :func:`t`. Persian is
the production default (``config.BOT_LANG = "fa"``); English is available via
``BOT_LANG=en``. Command NAMES stay latin (Telegram requires ascii ``/commands``) —
only their descriptions are translated, alongside every reply, button and poll.

``t(key, lang=None, **kwargs)`` resolves ``lang`` to ``settings.BOT_LANG`` when
omitted (so handler-level ``settings`` mocks never disturb language), falls back
to the other language when a key is missing in the active one, and raises
``KeyError`` only when a key is absent from both tables.
"""

from __future__ import annotations

from toop.config import settings

# Indicator → display label, per language. Stored indicator/callback codes stay
# ascii (see voting.INDICATOR_CODE); these are the words shown to humans.
_INDICATORS: dict[str, dict[str, str]] = {
    "fa": {
        "attack": "حمله",
        "receive": "دریافت",
        "block": "دفاع روی تور",
        "setting": "پاسور",
        "serve": "سرویس",
        "positioning": "جاگیری-تحرک",
    },
    "en": {
        "attack": "Attack",
        "receive": "Receive",
        "block": "Block",
        "setting": "Setting",
        "serve": "Serve",
        "positioning": "Positioning",
    },
}

# Score 1-5 → scale word, per language. The numeric score only ever travels in
# callback_data; players see these words on the buttons.
_SCORES: dict[str, dict[int, str]] = {
    "fa": {1: "خیلی ضعیف", 2: "ضعیف", 3: "متوسط", 4: "خوب", 5: "عالی"},
    "en": {1: "Very weak", 2: "Weak", 3: "OK", 4: "Good", 5: "Excellent"},
}


def _fa() -> dict[str, str]:
    return {
        # --- admin gate ---
        "admin.reject": "متاسفم، این دستور فقط برای ادمین است.",
        # --- command descriptions (short = menu blurb, usage = /help line) ---
        "cmd.start.short": "ثبت‌نام و شروع امتیازدهی",
        "cmd.start.usage": "/start — ثبت‌نام در ربات و شروع امتیازدهی به هم‌تیمی‌ها",
        "cmd.vote.short": "امتیازدهی به نفر بعدی",
        "cmd.vote.usage": "/vote — نمایش نفر بعدی برای امتیازدهی",
        "cmd.help.short": "نمایش دستورهای موجود",
        "cmd.help.usage": "/help — فهرست دستورهایی که می‌توانید استفاده کنید",
        "cmd.add_player.short": "افزودن بازیکن به فهرست",
        "cmd.add_player.usage": (
            '/add_player @username "نام نمایشی"  (یا /add_player <telegram_id> "نام نمایشی")'
        ),
        "cmd.remove_player.short": "حذف بازیکن از فهرست",
        "cmd.remove_player.usage": (
            "/remove_player (بدون آرگومان) برای دکمه‌ها، یا /remove_player @username"
        ),
        "cmd.pause_voting.short": "توقف موقت امتیازدهی به یک بازیکن",
        "cmd.pause_voting.usage": "/pause_voting <@username|telegram_id> <مدت مثل 2w یا 10d>",
        "cmd.disable_voting.short": "توقف امتیازدهی به یک بازیکن به‌طور نامحدود",
        "cmd.disable_voting.usage": "/disable_voting <@username|telegram_id>",
        "cmd.enable_voting.short": "بازگرداندن بازیکن به مجموعه امتیازدهی",
        "cmd.enable_voting.usage": "/enable_voting <@username|telegram_id>",
        "cmd.dk_report.short": "گزارش نرخ «نمی‌دانم»",
        "cmd.dk_report.usage": "/dk_report — فهرست نرخ «نمی‌دانم» هر بازیکن، بیشترین در ابتدا",
        "cmd.add_ghost.short": "افزودن بازیکن بدون حساب",
        "cmd.add_ghost.usage": '/add_ghost "نام نمایشی"',
        "cmd.link_player.short": "پیوند یک شبح به یک حساب واقعی",
        "cmd.link_player.usage": "/link_player <ghost_id> <@username|real_telegram_id>",
        "cmd.list_players.short": "نمایش فهرست فعال بازیکنان",
        "cmd.list_players.usage": "/list_players — نمایش فهرست فعال بازیکنان",
        "cmd.rename.short": "تغییر نام نمایشی بازیکن",
        "cmd.rename.usage": (
            '/rename (بدون آرگومان) برای دکمه‌ها، یا /rename <@username|telegram_id> "نام جدید"'
        ),
        "cmd.contacts.short": "فهرست همه کسانی که به ربات پیام داده‌اند",
        "cmd.contacts.usage": (
            "/contacts — فهرست همه کسانی که به ربات پیام داده‌اند، با علامت‌گذاری افراد خارج از فهرست"
        ),
        "cmd.open_session.short": "باز کردن یک جلسه",
        "cmd.open_session.usage": "/open_session [YYYY-MM-DD]  (پیش‌فرض: روز جلسه بعدی)",
        "cmd.sessions.short": "فهرست جلسه‌های اخیر",
        "cmd.sessions.usage": "/sessions — فهرست جلسه‌های اخیر و وضعیت آن‌ها",
        "cmd.nudge.short": "تهیه یادآور برای کم‌امتیازترین رای‌دهندگان",
        "cmd.nudge.usage": "/nudge — قالب‌های آماده برای یادآوری به کم‌فعال‌ترین رای‌دهندگان",
        "cmd.snapshot.short": "ساخت تیم‌های متوازن",
        "cmd.snapshot.usage": "/snapshot — ساخت تیم‌های متوازن از حاضرین فعلی",
        "cmd.swap.short": "جابه‌جایی دو بازیکن بین تیم‌ها",
        "cmd.swap.usage": "/swap @player_a @player_b",
        "cmd.change_player.short": "افزودن/حذف یک حاضر + توازن مجدد",
        "cmd.change_player.usage": (
            "/change_player +@username (افزودن) یا -@username "
            "(حذف)؛ بدون آرگومان برای دکمه‌ها (فقط در پیوی)"
        ),
        "cmd.publish.short": "انتشار تیم‌ها در گروه",
        "cmd.publish.usage": "/publish — ارسال تیم‌های فعلی به گروه",
        "cmd.health.short": "بررسی سلامت داده و امتیازها",
        "cmd.health.usage": "/health — نمایش شاخص‌های سلامت داده و امتیازدهی",
        "cmd.coverage.short": "گزارش پوشش امتیازدهی",
        "cmd.coverage.usage": "/coverage — نمایش کم‌امتیازترین بازیکنان در هر شاخص",
        "cmd.version.short": "نمایش کامیت و زمان کارکرد",
        "cmd.version.usage": "/version — نمایش SHA کامیت در حال اجرا و مدت کارکرد",
        "cmd.backup_db.short": "تهیه نسخه پشتیبان از پایگاه‌داده",
        "cmd.backup_db.usage": "/backup_db — نوشتن یک نسخه پشتیبان زمان‌دار SQLite",
        # --- voting / DM ---
        "vote.group_dm_nudge": "👋 همین‌جا در پیوی /vote را بزنید تا به هم‌تیمی‌ها امتیاز بدهید. 🏐",
        "vote.group_blocked_nudge": "با من (@{bot}) یک گفتگوی خصوصی شروع کن و آنجا /vote را بزن 🤫",
        "vote.no_prompts": (
            "🎉 فعلاً تمام شد — به همه امتیاز دادی. بعداً که فهرست بزرگ‌تر شد دوباره سر بزن."
        ),
        "vote.start_dm": (
            "سلام 👋 من توپ هستم — به متوازن کردن تیم‌های والیبال هفتگی کمک می‌کنم.\n\n"
            "هر وقت خواستی /vote را بزن تا به هم‌تیمی‌ها از ۱ تا ۵ در شش مهارت امتیاز بدهی "
            "(حمله، دریافت، دفاع روی تور، پاسور، سرویس، جاگیری-تحرک). "
            "هر زمان می‌توانی دوباره بزنی و امتیاز را تغییر دهی. "
            "هرچه بیشتر امتیاز بدهی، تیم‌ها دقیق‌تر می‌شوند. 🏐"
        ),
        "vote.start_group": (
            "👋 من توپ هستم. دکمه‌های حضوروغیاب را همین‌جا در گروه بزنید، "
            "و برای امتیازدهی به مهارت‌ها در پیوی /vote را بزنید."
        ),
        "vote.prompt": "به *{name}* امتیاز بده — *{label}*",
        "vote.btn_dont_know": "🤷 ندیدمش",
        "vote.btn_skip": "⏭ رد کردن",
        "vote.answer_skip": "رد شد ⏭",
        "vote.answer_dk": "رد شد 🤷",
        "vote.answer_recorded": "ثبت شد ✅",
        "vote.not_on_roster": "هنوز در فهرست نیستی — از ادمین بخواه اضافه‌ات کند.",
        "vote.nudge_none": "هنوز هیچ بازیکنی در فهرست نیست.",
        "vote.nudge_header": (
            "این‌ها را کپی/پیست کن تا به کم‌فعال‌ترین رای‌دهندگان یادآوری کنی. "
            "(فقط ارسال دستی — بدون پیام خودکار.)\n\n"
        ),
        "vote.nudge_template": (
            "--- {name} ({handle}) — {lifetime} امتیاز کل ---\n"
            "سلام {first}! هر وقت فرصت کردی، "
            "می‌شه به توپ در تلگرام پیام بدی و /vote را بزنی؟ "
            "به متوازن‌تر شدن تیم‌ها کمک می‌کنه. 🙏 حدود ۳۰ ثانیه طول می‌کشه."
        ),
        # --- group attendance / reservation polls ---
        "poll.attendance_question": (
            "آیا در برنامه والیبال دوشنبه آینده (از ساعت ۶ تا ۸) شرکت میکنید؟"
        ),
        "poll.attendance_yes": "بلی",
        "poll.attendance_no": "خیر",
        "poll.reservation_question": (
            "لیست انتظار - لطفا تنها در صورتی که موفق به شرکت در رای‌گیری فوق نشده‌اید و "
            "علاقه‌مند به حضور در برنامه والیبال دوشنبه آینده هستید، اینجا اعلام بفرمایید."
        ),
        "poll.reservation_waitlist": "مایل به قرار گرفتن در لیست انتظار هستم.",
        "poll.reservation_decline": "متاسفانه این جلسه نمیتوانم شرکت کنم.",
        "poll.capacity": "ظرفیت تکمیل شد.",
        "poll.quorum_header": "🎉 والیبال برگزار می‌شود 🏐",
        "poll.quorum_payment": (
            "\nلطفا در صورتی که در رای‌گیری حضور اعلام کرده‌اید مبلغ {amount} دلار "
            "به ایمیل زیر ای-ترنسفر کنید:\n{email}"
        ),
        "poll.quorum_like": "\nدوستان لطفا بعد از ارسال هزینه این پیام را لایک کنید.",
        "poll.quorum_sheet": "\nجدول حسابداری:\n{sheet}",
        "poll.drift_header": "⚠️ حضوروغیاب جلسه #{sid} تغییر کرد.",
        "poll.drift_added": "اضافه‌شده: {names}",
        "poll.drift_dropped": "حذف‌شده: {names}",
        "poll.drift_waitlist": "لیست انتظار: {names}",
        "poll.drift_fix": "برای اصلاح /change_player را اجرا کن.",
        # --- snapshot / teams / publish ---
        "snapshot.team_a_label": "🅰️ تیم آ",
        "snapshot.team_b_label": "🅱️ تیم ب",
        "snapshot.attending": "✅ حاضرین ({n}): ",
        "snapshot.cut": "\n⏳ خط‌خورده‌ها: {names}",
        "snapshot.proposed": "📅 *{date}* — تیم‌های پیشنهادی",
        "snapshot.composite_delta": "اختلاف ترکیبی: *{delta:.3f}* (آ={a:.2f}، ب={b:.2f})",
        "snapshot.calibration_conf": "اطمینان کالیبراسیون: *{conf}*",
        "snapshot.summary": (
            "اسنپ‌شات برای جلسه #{sid} ذخیره شد.{swap}\n"
            "با /swap جابه‌جا کن یا با /change_player تنظیم کن، با /publish منتشر کن.{cut}"
        ),
        "snapshot.setter_swap": " (جابه‌جایی پاسور اعمال شد)",
        "snapshot.summary_cut": "\n\nخط‌خورده‌ها: {names}",
        "snapshot.no_active_open": "جلسه فعالی نیست. با /open_session یکی باز کن.",
        "snapshot.no_rsvps": "هنوز کسی بله نداده — چیزی برای اسنپ‌شات نیست.",
        "snapshot.auto_ran": "⏰ اسنپ‌شات خودکار اجرا شد.\n\n{summary}",
        "snapshot.swap_usage": "روش استفاده: /swap @player_a @player_b",
        "snapshot.no_active": "جلسه فعالی نیست.",
        "snapshot.no_snapshot_yet": "هنوز اسنپ‌شاتی نیست. اول /snapshot را بزن.",
        "snapshot.usernames_not_roster": "یک یا هر دو نام‌کاربری در فهرست نیستند.",
        "snapshot.opposite_teams": "برای جابه‌جایی باید هر دو بازیکن در دو تیم مقابل باشند.",
        "snapshot.swapped": "🔁 جابه‌جا شد {a} ↔ {b}\n\n{text}",
        "snapshot.no_snapshot_publish": "اسنپ‌شاتی برای انتشار نیست.",
        "snapshot.group_unset": "GROUP_CHAT_ID تنظیم نشده — نمی‌توان در گروه منتشر کرد.",
        "snapshot.publish_body": (
            "🏐 تیم‌های {date}:\n\n{attendance}\n\n{text}\n\nمی‌بینیمتون روی زمین! 🙌"
        ),
        "snapshot.publish_failed": "انتشار ناموفق بود: {err}",
        "snapshot.published": "✅ جلسه #{sid} منتشر شد و {n} ردیف حضور ثبت شد.",
        # --- change_player ---
        "change.usage": (
            "روش استفاده: /change_player +@username (افزودن) یا /change_player -@username (حذف). "
            "بدون آرگومان برای دکمه‌ها."
        ),
        "change.dm_only": "/change_player را در گفتگوی خصوصی با من استفاده کن.",
        "change.pick_prompt": "یک بازیکن را برای حذف، یا یک نفر از لیست انتظار را برای ارتقا بزن:",
        "change.not_found": "{target} در فهرست پیدا نشد.",
        "change.none_left": "کسی برای توازن نمانده.",
        "change.btn_remove": "➖ {name}",
        "change.btn_promote": "⬆️ {name}",
        # --- sessions ---
        "sessions.open_usage": "روش استفاده: /open_session [YYYY-MM-DD]",
        "sessions.opened": "جلسه #{sid} برای {date} باز شد.",
        "sessions.none": "هنوز جلسه‌ای نیست.",
        "sessions.recent_header": "جلسه‌های اخیر:",
        "sessions.recent_row": "#{sid} {date} — {status}",
        # --- roster ---
        "roster.add_usage": (
            'روش استفاده: /add_player @username "نام نمایشی"  '
            '(یا /add_player <telegram_id> "نام نمایشی")'
        ),
        "roster.remove_usage": "روش استفاده: /remove_player @username",
        "roster.pause_usage": (
            "روش استفاده: /pause_voting <@username|telegram_id> <مدت مثل 2w یا 10d>"
        ),
        "roster.add_ghost_usage": 'روش استفاده: /add_ghost "نام نمایشی"',
        "roster.link_usage": "روش استفاده: /link_player <ghost_id> <@username|real_telegram_id>",
        "roster.rename_usage": (
            "روش استفاده: /rename (بدون آرگومان) برای دکمه‌ها، "
            'یا /rename <@username|telegram_id> "نام جدید"'
        ),
        "roster.rename_empty_roster": "هنوز بازیکنی در فهرست نیست — اول از /add_player استفاده کن.",
        "roster.dur_1week": "۱ هفته",
        "roster.dur_2weeks": "۲ هفته",
        "roster.dur_1month": "۱ ماه",
        "roster.no_username": "(بدون نام‌کاربری)",
        "roster.dm_to_add": "برای افزودن بازیکن به من پیوی بده. 🤫",
        "roster.no_new_contacts": (
            "مخاطب جدیدی برای افزودن نیست — از افراد بخواه اول به من /start بدهند."
        ),
        "roster.who_add": "چه کسی را می‌خواهی اضافه کنی؟",
        "roster.add_by_id_not_dmd": (
            "این کاربر (شناسه {id}) هنوز به ربات پیام نداده — اول باید /start بدهد "
            "تا بتوانم برای امتیازدهی پیام بدهم."
        ),
        "roster.cant_find_username": (
            "@{username} پیدا نشد. از او بخواه به من /start بدهد و دوباره امتحان کن. "
            'اگر نام‌کاربری تلگرام ندارد، /contacts را بزن و از /add_player <id> "نام" استفاده کن.'
        ),
        "roster.added": "{name} {handle} اضافه شد — در حال کالیبراسیون.{suffix}",
        "roster.revived_suffix": " (از حذف نرم بازگردانده شد)",
        "roster.send_display_name": "نام نمایشی برای {label} را بفرست:",
        "roster.contact_unavailable": "این مخاطب دیگر در دسترس نیست.",
        "roster.add_cancelled": "افزودن لغو شد — به‌جای نام یک دستور فرستادی.",
        "roster.name_empty": "نام نمی‌تواند خالی باشد — نام نمایشی را بفرست.",
        "roster.roster_empty_remove": "فهرست خالی است — کسی برای حذف نیست.",
        "roster.who_remove": "چه کسی را می‌خواهی حذف کنی؟",
        "roster.cant_find_username_short": "@{username} پیدا نشد.",
        "roster.removed_username": "@{username} حذف شد.",
        "roster.not_in_active": "@{username} در فهرست فعال نبود.",
        "roster.removed_name": "{name} حذف شد. ✅",
        "roster.player_gone": "این بازیکن دیگر در فهرست نیست.",
        "roster.roster_empty_pause": "فهرست خالی است — کسی برای توقف نیست.",
        "roster.who_pause": "چه کسی را می‌خواهی متوقف کنی؟",
        "roster.paused_until": (
            "{target} تا {date} متوقف شد — از دیگران خواسته نمی‌شود به او امتیاز دهند، "
            "ولی خودش می‌تواند رای دهد. برای لغو زودهنگام /enable_voting."
        ),
        "roster.cant_find_roster": "{target} در فهرست فعال پیدا نشد.",
        "roster.how_long_pause": "چه مدت {name} متوقف شود؟",
        "roster.paused_until_edit": (
            "⏸ {name} تا {date} متوقف شد — از دیگران خواسته نمی‌شود به او امتیاز دهند؛ "
            "خودش می‌تواند رای دهد. برای لغو زودهنگام /enable_voting."
        ),
        "roster.roster_empty_disable": "فهرست خالی است — کسی برای غیرفعال‌سازی نیست.",
        "roster.who_disable": "چه کسی را از مجموعه امتیازدهی خارج کنم؟",
        "roster.disabled": (
            "{target} از مجموعه امتیازدهی خارج شد — از دیگران خواسته نمی‌شود به او امتیاز دهند. "
            "خودش می‌تواند رای دهد. برای بازگرداندن /enable_voting."
        ),
        "roster.disabled_name": (
            "{name} از مجموعه امتیازدهی خارج شد 🚫 — برای بازگرداندن /enable_voting."
        ),
        "roster.nobody_paused": "الان کسی متوقف یا غیرفعال نیست. ✅",
        "roster.who_restore": "چه کسی را به مجموعه امتیازدهی بازگردانم؟",
        "roster.restored": "{target} به مجموعه امتیازدهی بازگردانده شد. ✅",
        "roster.restored_name": "{name} به مجموعه امتیازدهی بازگردانده شد. ✅",
        "roster.ghost_added": (
            "👻 شبح {name} اضافه شد (شناسه {id}). "
            "وقتی به تلگرام پیوست، /link_player {id} @their_username را اجرا کن."
        ),
        "roster.no_ghosts": "شبحی برای پیوند نیست. اول با /add_ghost یکی بساز.",
        "roster.which_ghost": "کدام شبح را می‌خواهی پیوند بدهی؟",
        "roster.not_a_ghost": (
            "{id} یک بازیکن شبح نیست. /list_players را بزن و از یک شناسه 👻 استفاده کن."
        ),
        "roster.cant_find_start": "@{username} پیدا نشد. از او بخواه اول به من /start بدهد.",
        "roster.real_not_dmd": (
            "این کاربر (شناسه {id}) هنوز به ربات پیام نداده — باید /start بدهد "
            "تا بتوانم برای امتیازدهی پیام بدهم."
        ),
        "roster.link_summary": (
            "🔗 شبح {name} → شناسه {id} پیوند خورد. {scores} امتیاز، "
            "{ratings} رتبه، {rsvps} پاسخ حضور، {attendance} ردیف حضور منتقل شد."
        ),
        "roster.not_ghost_anymore": "این دیگر یک بازیکن شبح نیست.",
        "roster.nobody_new_dmd": (
            "هنوز کسی تازه به ربات پیام نداده — از آن‌ها "
            "بخواه /start بدهند، بعد دوباره /link_player."
        ),
        "roster.link_to_which": "👻 {name} را به کدام حساب پیوند بدهم؟",
        "roster.ghost_unavailable": "این شبح دیگر در دسترس نیست.",
        "roster.dk_none": "هنوز هیچ بازیکنی در فهرست نیست.",
        "roster.dk_header": "🤷 گزارش «نمی‌دانم» (بیشترین نرخ در ابتدا):",
        "roster.dk_row": "{i}. {name} — {dk}/{total} نمی‌دانم ({pct}%)",
        "roster.roster_empty_list": "فهرست خالی است. برای شروع از /add_player استفاده کن.",
        "roster.list_header": "فهرست بازیکنان:",
        "roster.calibrating": "🟡 در حال کالیبراسیون",
        "roster.ghost_tag": "👻 شبح",
        "roster.voting_disabled_tag": " — 🚫 امتیازدهی غیرفعال",
        "roster.voting_paused_tag": " — ⏸ امتیازدهی متوقف",
        "roster.list_row": "{i}. {name} {handle} — {marker}{tags}",
        "roster.contacts_none": "هنوز کسی به من پیام نداده. از افراد بخواه /start بفرستند.",
        "roster.contacts_header": "مخاطبین (کسانی که به من پیام داده‌اند):",
        "roster.contact_row": "{i}. {handle} ({name}) — اولین مشاهده {date}",
        "roster.contact_row_new": "{i}. {handle} ({name}) — اولین مشاهده {date}  🆕 خارج از فهرست",
        "roster.contacts_legend": (
            "\n🆕 = قابل افزودن با /add_player ({n} نفر هنوز در فهرست نیستند)."
        ),
        "roster.dm_to_rename": "برای تغییر نام بازیکن‌ها به من پیوی بده. 🤫",
        "roster.who_rename": "نام چه کسی را می‌خواهی تغییر بدهی؟",
        "roster.no_active_player_id": "بازیکن فعالی با شناسه {id} نیست.",
        "roster.username_not_active": "@{username} در فهرست فعال نیست.",
        "roster.renamed": "{old} → {new} تغییر نام یافت ✅",
        "roster.send_new_name": "نام نمایشی جدید برای {name} را بفرست:",
        "roster.rename_cancelled": "تغییر نام لغو شد — به‌جای نام یک دستور فرستادی.",
        "roster.new_name_empty": "نام نمی‌تواند خالی باشد — نام نمایشی جدید را بفرست.",
        # --- health / coverage ---
        "health.roster_empty": "فهرست خالی است.",
        "coverage.not_enough": "بازیکن کافی برای محاسبه پوشش نیست.",
        "coverage.header": "شکاف‌های پوشش (کم‌امتیازترین بازیکنان):",
        "coverage.row": "• {name} — {labels}",
        # --- alerts ---
        "alert.header": "⚠️ بازیکنان سخت‌امتیاز (🤷 زیاد). توقف امتیازدهی به آن‌ها را در نظر بگیر:",
        "alert.row": "• {name} — {dk}/{total} نمی‌دانم ({pct}%) → /pause_voting {id} {days}d",
        # --- ops ---
        "ops.version": "توپ کامیت `{sha}` · کارکرد {uptime}",
        "ops.db_not_found": "فایل پایگاه‌داده در {path} پیدا نشد",
        "ops.backup_saved": "💾 نسخه پشتیبان ذخیره شد → `{path}` ({kb} کیلوبایت)",
    }


def _en() -> dict[str, str]:
    return {
        "admin.reject": "Sorry, this command is admin-only.",
        "cmd.start.short": "Register and start rating",
        "cmd.start.usage": "/start — register with the bot and start rating teammates",
        "cmd.vote.short": "Rate the next player",
        "cmd.vote.usage": "/vote — show the next teammate to rate",
        "cmd.help.short": "Show available commands",
        "cmd.help.usage": "/help — list the commands you can use",
        "cmd.add_player.short": "Add a player to the roster",
        "cmd.add_player.usage": (
            '/add_player @username "Display Name"  (or /add_player <telegram_id> "Display Name")'
        ),
        "cmd.remove_player.short": "Remove a player from the roster",
        "cmd.remove_player.usage": (
            "/remove_player (no args) for buttons, or /remove_player @username"
        ),
        "cmd.pause_voting.short": "Pause rating a player",
        "cmd.pause_voting.usage": "/pause_voting <@username|telegram_id> <duration like 2w or 10d>",
        "cmd.disable_voting.short": "Stop rating a player indefinitely",
        "cmd.disable_voting.usage": "/disable_voting <@username|telegram_id>",
        "cmd.enable_voting.short": "Restore a player to the rating pool",
        "cmd.enable_voting.usage": "/enable_voting <@username|telegram_id>",
        "cmd.dk_report.short": "Don't-know rate report",
        "cmd.dk_report.usage": "/dk_report — list each player's don't-know rate, highest first",
        "cmd.add_ghost.short": "Add an accountless player",
        "cmd.add_ghost.usage": '/add_ghost "Display Name"',
        "cmd.link_player.short": "Link a ghost to a real account",
        "cmd.link_player.usage": "/link_player <ghost_id> <@username|real_telegram_id>",
        "cmd.list_players.short": "List the active roster",
        "cmd.list_players.usage": "/list_players — show the active roster",
        "cmd.rename.short": "Rename a player's display name",
        "cmd.rename.usage": (
            '/rename (no args) for buttons, or /rename <@username|telegram_id> "New Name"'
        ),
        "cmd.contacts.short": "List everyone who DM'd the bot",
        "cmd.contacts.usage": (
            "/contacts — list everyone who has DM'd the bot, flagging who's not on the roster"
        ),
        "cmd.open_session.short": "Open a session",
        "cmd.open_session.usage": (
            "/open_session [YYYY-MM-DD]  (defaults to the next session weekday)"
        ),
        "cmd.sessions.short": "List recent sessions",
        "cmd.sessions.usage": "/sessions — list recent sessions and their status",
        "cmd.nudge.short": "Draft nudges for low-completion voters",
        "cmd.nudge.usage": "/nudge — copy/paste templates to nudge the lowest-completion voters",
        "cmd.snapshot.short": "Generate balanced teams",
        "cmd.snapshot.usage": "/snapshot — build balanced teams from the current yes-RSVPs",
        "cmd.swap.short": "Swap two players between teams",
        "cmd.swap.usage": "/swap @player_a @player_b",
        "cmd.change_player.short": "Add/remove an attendee + rebalance",
        "cmd.change_player.usage": (
            "/change_player +@username (add) or -@username (remove); no args for buttons (DM only)"
        ),
        "cmd.publish.short": "Publish teams to the group",
        "cmd.publish.usage": "/publish — post the current teams to the group chat",
        "cmd.health.short": "Data and rating health check",
        "cmd.health.usage": "/health — show data and rating health metrics",
        "cmd.coverage.short": "Rating coverage report",
        "cmd.coverage.usage": "/coverage — show the least-rated players per indicator",
        "cmd.version.short": "Show commit and uptime",
        "cmd.version.usage": "/version — show the running commit SHA and uptime",
        "cmd.backup_db.short": "Back up the database",
        "cmd.backup_db.usage": "/backup_db — write a timestamped SQLite backup",
        "vote.group_dm_nudge": "👋 Tap /vote right here in our DM to rate teammates. 🏐",
        "vote.group_blocked_nudge": "start a DM with me (@{bot}) and tap /vote there 🤫",
        "vote.no_prompts": (
            "🎉 All done for now — you've rated everyone. Check back later as the roster grows."
        ),
        "vote.start_dm": (
            "Hi 👋 I'm توپ — I help balance our weekly volleyball teams.\n\n"
            "Tap /vote any time to rate your teammates 1–5 on six skills "
            "(attack, receive, block, setting, serve, positioning). "
            "You can re-tap any time to change a score. "
            "The more you rate, the more accurate the teams. 🏐"
        ),
        "vote.start_group": (
            "👋 I'm توپ. Tap RSVP buttons here in the group, and DM me to /vote on player skills."
        ),
        "vote.prompt": "Rate *{name}* — *{label}*",
        "vote.btn_dont_know": "🤷 Haven't seen",
        "vote.btn_skip": "⏭ Skip",
        "vote.answer_skip": "Skipped ⏭",
        "vote.answer_dk": "Skipped 🤷",
        "vote.answer_recorded": "Recorded ✅",
        "vote.not_on_roster": "You're not on the roster yet — ask the admin to add you.",
        "vote.nudge_none": "No players on the roster yet.",
        "vote.nudge_header": (
            "Copy/paste these to nudge the lowest-completion voters. "
            "(Manual sends only — no auto-DMs.)\n\n"
        ),
        "vote.nudge_template": (
            "--- {name} ({handle}) — {lifetime} lifetime ratings ---\n"
            "Hey {first}! Whenever you get a sec, "
            "could you ping توپ on Telegram and run /vote? "
            "It helps me balance teams better. 🙏 Takes ~30s."
        ),
        "poll.attendance_question": (
            "Are you joining next Monday's volleyball session (6 to 8 PM)?"
        ),
        "poll.attendance_yes": "Yes",
        "poll.attendance_no": "No",
        "poll.reservation_question": (
            "Waitlist — only if you missed the poll above and still want to join next Monday's "
            "volleyball session, let us know here."
        ),
        "poll.reservation_waitlist": "I'd like to be on the waitlist.",
        "poll.reservation_decline": "Sorry, I can't make this session.",
        "poll.capacity": "Capacity reached.",
        "poll.quorum_header": "🎉 Volleyball is on 🏐",
        "poll.quorum_payment": (
            "\nIf you said yes in the poll, please e-transfer {amount} dollars "
            "to the email below:\n{email}"
        ),
        "poll.quorum_like": "\nPlease like this message once you've sent payment.",
        "poll.quorum_sheet": "\nAccounting sheet:\n{sheet}",
        "poll.drift_header": "⚠️ Attendance changed for session #{sid}.",
        "poll.drift_added": "Added: {names}",
        "poll.drift_dropped": "Dropped: {names}",
        "poll.drift_waitlist": "Waitlist: {names}",
        "poll.drift_fix": "Run /change_player to fix.",
        "snapshot.team_a_label": "🅰️ Team A",
        "snapshot.team_b_label": "🅱️ Team B",
        "snapshot.attending": "✅ Attending ({n}): ",
        "snapshot.cut": "\n⏳ Cut: {names}",
        "snapshot.proposed": "📅 *{date}* — proposed teams",
        "snapshot.composite_delta": "Composite Δ: *{delta:.3f}* (A={a:.2f}, B={b:.2f})",
        "snapshot.calibration_conf": "Calibration confidence: *{conf}*",
        "snapshot.summary": (
            "Snapshot saved for session #{sid}.{swap}\n"
            "Swap with /swap or adjust with /change_player, ship with /publish.{cut}"
        ),
        "snapshot.setter_swap": " (setter swap applied)",
        "snapshot.summary_cut": "\n\nCut: {names}",
        "snapshot.no_active_open": "No active session. Open one with /open_session.",
        "snapshot.no_rsvps": "No yes-RSVPs yet — nothing to snapshot.",
        "snapshot.auto_ran": "⏰ Auto-snapshot ran.\n\n{summary}",
        "snapshot.swap_usage": "Usage: /swap @player_a @player_b",
        "snapshot.no_active": "No active session.",
        "snapshot.no_snapshot_yet": "No snapshot yet. Run /snapshot first.",
        "snapshot.usernames_not_roster": "One or both usernames aren't on the roster.",
        "snapshot.opposite_teams": "Both players must be on opposite teams to swap.",
        "snapshot.swapped": "🔁 Swapped {a} ↔ {b}\n\n{text}",
        "snapshot.no_snapshot_publish": "No snapshot to publish.",
        "snapshot.group_unset": "GROUP_CHAT_ID is unset — can't publish to group.",
        "snapshot.publish_body": (
            "🏐 Teams for {date}:\n\n{attendance}\n\n{text}\n\nSee you on court! 🙌"
        ),
        "snapshot.publish_failed": "Failed to publish: {err}",
        "snapshot.published": "✅ Published session #{sid} and recorded {n} attendance rows.",
        "change.usage": (
            "Usage: /change_player +@username (add) or /change_player -@username (remove). "
            "Send with no args for buttons."
        ),
        "change.dm_only": "Use /change_player in a private chat with me.",
        "change.pick_prompt": "Tap a player to remove, or a waitlister to promote:",
        "change.not_found": "Couldn't find {target} on the roster.",
        "change.none_left": "No attendees left to balance.",
        "change.btn_remove": "➖ {name}",
        "change.btn_promote": "⬆️ {name}",
        "sessions.open_usage": "Usage: /open_session [YYYY-MM-DD]",
        "sessions.opened": "Session #{sid} opened for {date}.",
        "sessions.none": "No sessions yet.",
        "sessions.recent_header": "Recent sessions:",
        "sessions.recent_row": "#{sid} {date} — {status}",
        "roster.add_usage": (
            'Usage: /add_player @username "Display Name"  '
            '(or /add_player <telegram_id> "Display Name")'
        ),
        "roster.remove_usage": "Usage: /remove_player @username",
        "roster.pause_usage": (
            "Usage: /pause_voting <@username|telegram_id> <duration like 2w or 10d>"
        ),
        "roster.add_ghost_usage": 'Usage: /add_ghost "Display Name"',
        "roster.link_usage": "Usage: /link_player <ghost_id> <@username|real_telegram_id>",
        "roster.rename_usage": (
            'Usage: /rename (no args) for buttons, or /rename <@username|telegram_id> "New Name"'
        ),
        "roster.rename_empty_roster": "No players on the roster yet — use /add_player first.",
        "roster.dur_1week": "1 week",
        "roster.dur_2weeks": "2 weeks",
        "roster.dur_1month": "1 month",
        "roster.no_username": "(no username)",
        "roster.dm_to_add": "DM me to add players. 🤫",
        "roster.no_new_contacts": "No new contacts to add — ask people to DM me /start first.",
        "roster.who_add": "Who do you want to add?",
        "roster.add_by_id_not_dmd": (
            "That user (id {id}) hasn't DM'd the bot yet — they must "
            "DM /start first so I can message them for voting."
        ),
        "roster.cant_find_username": (
            "Couldn't find @{username}. Ask them to DM me /start, then try again. "
            "If they have no Telegram username, run /contacts and use "
            '/add_player <id> "Name" instead.'
        ),
        "roster.added": "Added {name} {handle} — calibrating.{suffix}",
        "roster.revived_suffix": " (revived from soft-delete)",
        "roster.send_display_name": "Send the display name for {label}:",
        "roster.contact_unavailable": "That contact is no longer available.",
        "roster.add_cancelled": "Add cancelled — you sent a command instead of a name.",
        "roster.name_empty": "Name can't be empty — send the display name.",
        "roster.roster_empty_remove": "Roster is empty — nobody to remove.",
        "roster.who_remove": "Who do you want to remove?",
        "roster.cant_find_username_short": "Couldn't find @{username}.",
        "roster.removed_username": "Removed @{username}.",
        "roster.not_in_active": "@{username} wasn't in the active roster.",
        "roster.removed_name": "Removed {name}. ✅",
        "roster.player_gone": "That player is no longer on the roster.",
        "roster.roster_empty_pause": "Roster is empty — nobody to pause.",
        "roster.who_pause": "Who do you want to pause?",
        "roster.paused_until": (
            "Paused {target} until {date} — others won't be asked to "
            "rate them, but they can still vote. /enable_voting to undo early."
        ),
        "roster.cant_find_roster": "Couldn't find {target} on the active roster.",
        "roster.how_long_pause": "How long to pause {name}?",
        "roster.paused_until_edit": (
            "⏸ Paused {name} until {date} — others won't be asked to "
            "rate them; they can still vote. /enable_voting to undo early."
        ),
        "roster.roster_empty_disable": "Roster is empty — nobody to disable.",
        "roster.who_disable": "Who do you want to pull from the rating pool?",
        "roster.disabled": (
            "Disabled {target} from the rating pool — others won't be asked to "
            "rate them. They can still vote. /enable_voting to restore."
        ),
        "roster.disabled_name": (
            "Disabled {name} from the rating pool 🚫 — /enable_voting to restore."
        ),
        "roster.nobody_paused": "Nobody is paused or disabled right now. ✅",
        "roster.who_restore": "Who do you want to restore to the rating pool?",
        "roster.restored": "Restored {target} to the rating pool. ✅",
        "roster.restored_name": "Restored {name} to the rating pool. ✅",
        "roster.ghost_added": (
            "👻 Added ghost {name} (id {id}). "
            "When they join Telegram, run /link_player {id} @their_username."
        ),
        "roster.no_ghosts": "No ghost players to link. Add one with /add_ghost first.",
        "roster.which_ghost": "Which ghost do you want to link?",
        "roster.not_a_ghost": "{id} isn't a ghost player. Run /list_players and use a 👻 id.",
        "roster.cant_find_start": "Couldn't find @{username}. Ask them to DM me /start first.",
        "roster.real_not_dmd": (
            "That user (id {id}) hasn't DM'd the bot yet — they must /start so "
            "I can message them for voting."
        ),
        "roster.link_summary": (
            "🔗 Linked ghost {name} → id {id}. Moved "
            "{scores} scores, {ratings} ratings, {rsvps} RSVPs, {attendance} attendance rows."
        ),
        "roster.not_ghost_anymore": "That isn't a ghost player anymore.",
        "roster.nobody_new_dmd": (
            "Nobody new has DM'd the bot yet — ask them to /start, then /link_player again."
        ),
        "roster.link_to_which": "Link 👻 {name} to which account?",
        "roster.ghost_unavailable": "That ghost is no longer available.",
        "roster.dk_none": "No players on the roster yet.",
        "roster.dk_header": "🤷 Don't-know report (highest rate first):",
        "roster.dk_row": "{i}. {name} — {dk}/{total} don't-know ({pct}%)",
        "roster.roster_empty_list": "Roster is empty. Use /add_player to start.",
        "roster.list_header": "Roster:",
        "roster.calibrating": "🟡 calibrating",
        "roster.ghost_tag": "👻 ghost",
        "roster.voting_disabled_tag": " — 🚫 voting disabled",
        "roster.voting_paused_tag": " — ⏸ voting paused",
        "roster.list_row": "{i}. {name} {handle} — {marker}{tags}",
        "roster.contacts_none": "Nobody has DM'd me yet. Ask people to send /start.",
        "roster.contacts_header": "Contacts (people who've DM'd me):",
        "roster.contact_row": "{i}. {handle} ({name}) — first seen {date}",
        "roster.contact_row_new": "{i}. {handle} ({name}) — first seen {date}  🆕 not on roster",
        "roster.contacts_legend": ("\n🆕 = available to /add_player ({n} not yet on the roster)."),
        "roster.dm_to_rename": "DM me to rename players. 🤫",
        "roster.who_rename": "Who do you want to rename?",
        "roster.no_active_player_id": "No active player with id {id}.",
        "roster.username_not_active": "@{username} isn't on the active roster.",
        "roster.renamed": "Renamed {old} → {new} ✅",
        "roster.send_new_name": "Send the new display name for {name}:",
        "roster.rename_cancelled": "Rename cancelled — you sent a command instead of a name.",
        "roster.new_name_empty": "Name can't be empty — send the new display name.",
        "health.roster_empty": "Roster is empty.",
        "coverage.not_enough": "Not enough players to compute coverage.",
        "coverage.header": "Coverage gaps (least-rated players):",
        "coverage.row": "• {name} — {labels}",
        "alert.header": "⚠️ Hard-to-rate players (lots of 🤷). Consider pausing them from voting:",
        "alert.row": "• {name} — {dk}/{total} don't-know ({pct}%) → /pause_voting {id} {days}d",
        "ops.version": "توپ commit `{sha}` · uptime {uptime}",
        "ops.db_not_found": "DB file not found at {path}",
        "ops.backup_saved": "💾 Backup saved → `{path}` ({kb} KB)",
    }


MESSAGES: dict[str, dict[str, str]] = {"fa": _fa(), "en": _en()}

_FALLBACK = {"fa": "en", "en": "fa"}


def _resolve_lang(lang: str | None) -> str:
    """Pick the language: explicit arg wins, else the runtime setting, else fa."""
    chosen = (lang or settings.BOT_LANG or "fa").lower()
    return chosen if chosen in MESSAGES else "fa"


def t(key: str, lang: str | None = None, /, **kwargs: object) -> str:
    """Resolve a catalog key to text in ``lang`` (or ``settings.BOT_LANG``).

    Falls back to the other language when the key is missing in the active one;
    raises ``KeyError`` only when the key is absent from both tables. Remaining
    ``kwargs`` are interpolated with :meth:`str.format`.
    """
    chosen = _resolve_lang(lang)
    table = MESSAGES[chosen]
    template = table[key] if key in table else MESSAGES[_FALLBACK[chosen]][key]
    return template.format(**kwargs) if kwargs else template


def indicator_label(indicator: str, lang: str | None = None) -> str:
    """Human display name for a skill indicator (falls back to the raw code)."""
    chosen = _resolve_lang(lang)
    return _INDICATORS[chosen].get(indicator, indicator)


def score_word(score: int, lang: str | None = None) -> str:
    """Persian/English scale word for a 1-5 score."""
    chosen = _resolve_lang(lang)
    return _SCORES[chosen][score]
