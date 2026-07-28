"""Ask the exchange whether the API key can withdraw. Roadmap P2.4.

The control plane showed customers that withdrawal permission was disabled on
their key. Nothing had ever checked. `/api/v1/exchange/test` set
``withdraw_disabled_attested = True`` unconditionally after a successful probe
and stored it inside the ``capabilities`` dict — beside facts the probe had
actually measured — so a checkbox the user ticked during onboarding became
indistinguishable from a verified property of their key. The probe itself was
honest about this and emitted ``withdraw_permission_checked: False``; the API
layer overwrote it.

Both venues expose the answer:

* OKX ``GET /api/v5/account/config`` returns ``perm``, a comma-separated list
  such as ``read_only,trade`` — ``withdraw`` present means the key can move
  funds out.
* Binance ``GET /sapi/v1/account/apiRestrictions`` returns
  ``enableWithdrawals``.

Three rules this module is built around, because the failure it replaces was a
confident answer nobody had earned:

1. **The result is tri-state.** ``True`` (venue says withdrawals are off),
   ``False`` (venue says they are ON — a finding, not a formality) and ``None``
   (could not determine).
2. **Every failure path yields ``None``, never ``True``.** An unsupported venue,
   a network error, a permission error on the check itself, a response shape we
   do not recognise — all unknown. The only thing that produces ``True`` is the
   venue saying so.
3. **This is not the user's attestation.** That claim keeps its own field. Two
   different things that were being stored as one.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

#: Venue ids whose keys this module knows how to interrogate. Anything else
#: reports unknown rather than being assumed safe.
SUPPORTED = ("okx", "binance", "binanceusdm")


def _okx(exchange: Any) -> Optional[bool]:
    """OKX: `perm` is a comma-separated permission list on the account config."""
    response = exchange.privateGetAccountConfig()
    rows = (response or {}).get("data") or []
    if not rows:
        return None
    perms = str(rows[0].get("perm") or "")
    if not perms:
        return None
    granted = {p.strip().lower() for p in perms.split(",") if p.strip()}
    if not granted:
        return None
    return "withdraw" not in granted


def _binance(exchange: Any) -> Optional[bool]:
    """Binance: `enableWithdrawals` on the API restrictions endpoint."""
    response = exchange.sapiGetAccountApiRestrictions()
    if not isinstance(response, dict) or "enableWithdrawals" not in response:
        return None
    return not bool(response["enableWithdrawals"])


_CHECKS: Dict[str, Callable[[Any], Optional[bool]]] = {
    "okx": _okx,
    "binance": _binance,
    "binanceusdm": _binance,
}


def check_withdraw_disabled(client: Any, exchange_id: str) -> Dict[str, Any]:
    """Ask the venue whether this key's withdrawal permission is off.

    Returns ``{"withdraw_disabled": True|False|None, "checked": bool,
    "detail": str}``. ``checked`` is False whenever the answer is unknown, so a
    caller cannot mistake "we could not tell" for "we asked and it is fine".
    """
    result: Dict[str, Any] = {
        "withdraw_disabled": None,
        "checked": False,
        "detail": "",
    }
    venue = str(exchange_id or "").strip().lower()
    check = _CHECKS.get(venue)
    if check is None:
        result["detail"] = f"no withdrawal-permission check implemented for {venue or 'unknown venue'}"
        return result

    exchange = getattr(client, "exchange", None)
    if exchange is None:
        result["detail"] = "client exposes no venue handle to query"
        return result

    try:
        answer = check(exchange)
    except Exception as exc:  # noqa: BLE001 - any failure is "unknown", by design
        # Including the case where the key lacks permission to read its own
        # restrictions. That is still not evidence that withdrawals are off.
        result["detail"] = f"{type(exc).__name__}: {exc}"
        return result

    if answer is None:
        result["detail"] = "venue response did not contain a usable permission list"
        return result

    result["withdraw_disabled"] = bool(answer)
    result["checked"] = True
    result["detail"] = (
        "venue reports withdrawals disabled for this key" if answer
        else "venue reports withdrawals ENABLED for this key"
    )
    return result
