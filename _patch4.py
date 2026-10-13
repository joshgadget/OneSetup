import pathlib
cp = pathlib.Path(r"C:\Users\Hi\trading\engine\config.py")
t = cp.read_text(encoding="utf-8")
old = '''    fxcm_access_token: str = ""
    fxcm_host: str = "api-demo.fxcm.com"
    fxcm_price_side: str = "mid"

    deriv_app_id: str = ""
'''
new = '''    fxcm_access_token: str = ""
    fxcm_host: str = "api-demo.fxcm.com"
    fxcm_price_side: str = "mid"
    fxcm_login_id: str = ""
    fxcm_password: str = ""
    fxcm_iam_host: str = "endpoints-demo.fxcm.com"

    deriv_app_id: str = ""

    # Which provider serves FX and gold. Switching broker feed = one env var.
    forex_venue: str = "deriv"
    gold_venue: str = "deriv"
'''
assert old in t
t = t.replace(old, new, 1)
cp.write_text(t, encoding="utf-8", newline="\n")
print("config.py patched")