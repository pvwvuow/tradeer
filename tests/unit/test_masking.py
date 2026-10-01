from app.observability.masking import MASK, SecretMasker, is_sensitive_key

# Token-shaped test values are assembled from parts so that no complete token literal exists
# in the repository (secret scanners would rightly flag one).
GITHUB_CLASSIC = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
GITHUB_FINE = "github" + "_pat_" + "11ABCDEFG0" + "x" * 40
OPENAI_KEY = "sk" + "-proj-" + "a1B2" * 8
SUPABASE_JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJyb2xlIjoiYW5vbiJ9" + "." + "s1gnatureXYZ123"
TELEGRAM_TOKEN = "123456789" + ":" + "AAH" + "q" * 32
AWS_KEY_ID = "AKIA" + "ABCDEFGHIJKLMNOP"
SLACK_TOKEN = "xoxb" + "-1234567890-abcdefghij"

KNOWN_TOKENS = (
    GITHUB_CLASSIC,
    GITHUB_FINE,
    OPENAI_KEY,
    SUPABASE_JWT,
    TELEGRAM_TOKEN,
    AWS_KEY_ID,
    SLACK_TOKEN,
)


def test_known_token_formats_are_masked_anywhere_in_text() -> None:
    masker = SecretMasker()
    for token in KNOWN_TOKENS:
        masked = masker.mask(f"connecting with {token}, then continuing")
        assert token not in masked, token
        assert MASK in masked
        assert masked.endswith(", then continuing")


def test_bearer_tokens_are_masked() -> None:
    masked = SecretMasker().mask("Authorization: Bearer abc.def-ghi_jkl123")
    assert "abc.def-ghi_jkl123" not in masked
    assert masked.startswith("Authorization: ")


def test_sensitive_key_value_pairs_are_masked_in_every_common_shape() -> None:
    masker = SecretMasker()
    samples = {
        "password=hunter22 next": ("hunter22", "password=*** next"),
        'login ok, "password": "two words"}': ("two words", 'login ok, "password": "***"}'),
        "{'api_key': 'k-123456'}": ("k-123456", "{'api_key': '***'}"),
        "GET /x?access_token=abcdef&page=2": ("abcdef", "GET /x?access_token=***&page=2"),
        "telegram pin: 4821": ("4821", "telegram pin: ***"),
        "SUPABASE_SERVICE_ROLE_KEY=zzz999": ("zzz999", "SUPABASE_SERVICE_ROLE_KEY=***"),
        "secret = s3cr3t;": ("s3cr3t", "secret = ***;"),
    }
    for text, (secret, expected) in samples.items():
        masked = masker.mask(text)
        assert secret not in masked, text
        assert masked == expected, text


def test_passwords_inside_urls_are_masked() -> None:
    masked = SecretMasker().mask("db at postgresql://trader:p4ss-w0rd@db.example.com:5432/x")
    assert "p4ss-w0rd" not in masked
    assert "postgresql://trader:***@db.example.com:5432/x" in masked


def test_registered_values_are_masked_even_without_a_key() -> None:
    masker = SecretMasker()
    masker.register("correct-horse-battery")
    masker.register("abc")  # too short to register safely: ignored
    masked = masker.mask("the password is correct-horse-battery! abc")
    assert masked == f"the password is {MASK}! abc"
    masker.forget("correct-horse-battery")
    assert masker.mask("correct-horse-battery") == "correct-horse-battery"


def test_ordinary_trading_text_is_left_alone() -> None:
    text = (
        "EURUSD buy 0.10 lots at 1.08345 (sl 1.08100), max_tokens=500, pin bar on H1, "
        "task-1234, risk-free, login 51234567, author=me"
    )
    assert SecretMasker().mask(text) == text


def test_masking_is_idempotent() -> None:
    masker = SecretMasker()
    samples = [f"token={token} and Bearer {token}" for token in KNOWN_TOKENS]
    samples += ["password: 'x y z'", "https://u:pw123@host/path", "pin=1234"]
    for sample in samples:
        once = masker.mask(sample)
        assert masker.mask(once) == once


def test_structured_values_are_masked_recursively_and_made_json_safe() -> None:
    masker = SecretMasker()
    value = {
        "login": 51234567,
        "password": "hunter22",
        "nested": {"api_key": 12345, "note": f"token={GITHUB_CLASSIC}", "empty_token": ""},
        "items": (f"Bearer {OPENAI_KEY}", 1.5, None, True),
        "raw": b"\x00\x01",
    }
    assert masker.mask_value(value) == {
        "login": 51234567,
        "password": MASK,
        "nested": {"api_key": MASK, "note": f"token={MASK}", "empty_token": ""},
        "items": [f"Bearer {MASK}", 1.5, None, True],
        "raw": "<2 bytes>",
    }


def test_sensitive_key_names() -> None:
    sensitive = (
        "password",
        "api_key",
        "apiKey",
        "telegramToken",
        "supabase_anon_key",
        "service_role_key",
        "Authorization",
        "credentials",
        "pin",
        "telegram_pin",
        "client_secret",
    )
    harmless = ("login", "symbol", "max_tokens", "pin_bar", "author", "spin", "key_level")
    for key in sensitive:
        assert is_sensitive_key(key), key
    for key in harmless:
        assert not is_sensitive_key(key), key
