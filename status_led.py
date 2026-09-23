"""
Statuslampa - lyser sa lange app.py kor.

Helt tyst modul: saknas lgpio, ar pinnen upptagen eller finns ingen diod
inkopplad sa hander ingenting alls. Inga felmeddelanden, inga undantag ut.
"""

import config

_handle = None


def on():
    """Tand statuslampan. Gor inget om nagot inte fungerar."""
    global _handle
    if _handle is not None:
        return
    try:
        import lgpio
        h = lgpio.gpiochip_open(config.GPIO_CHIP)
        try:
            lgpio.gpio_claim_output(h, config.STATUS_LED_PIN, 1)
        except Exception:
            try:
                lgpio.gpiochip_close(h)
            except Exception:
                pass
            return
        _handle = h
    except Exception:
        _handle = None


def off():
    """Slack statuslampan och slapp pinnen. Gor inget om nagot inte fungerar."""
    global _handle
    h, _handle = _handle, None
    if h is None:
        return
    try:
        import lgpio
        try:
            lgpio.gpio_write(h, config.STATUS_LED_PIN, 0)
        except Exception:
            pass
        try:
            lgpio.gpio_free(h, config.STATUS_LED_PIN)
        except Exception:
            pass
        lgpio.gpiochip_close(h)
    except Exception:
        pass
