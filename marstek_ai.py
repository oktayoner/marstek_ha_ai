"""
Marstek AI Scheduler v10.12 - Master Automation (Pyscript)
================================================================================
- Drievoudige Hardware-Aansturing: max_charge/discharge_power,
  allow_charge/discharge EN target_grid_power samen in elke tak.
- Hardware-Managed 0-op-de-meter via target_grid_power en dynamische marges.
- Thermische opslag met prijs-bewuste boiler (inclusief LSC slimme stekker).
- Batterij Slijtage- & Rendement-berekening in ontlaaddrempel.
- Resterende Zonne-prognose & Slimme 13:15 Tibber-schema herberekening (incl. tomorrow).
- Dynamische Avond- & Nacht-Vloer bescherming.
- Hoofdzekering- & Grootverbruiker-beveiliging.
- Schema-freshness fail-safe (3u) die batterij niet uitzet maar op veilige
  0-op-de-meter zet met discharge vrij.
- UITGEBREIDE ACTIE-LOG: per 5-min regelloop een mens-leesbare uitleg
  van WAT er gebeurt en WAAROM, opgeslagen in HELPER_STATUS (kort)
  én in HELPER_SCHEMA attributes ("uitleg" + "actie_log").
- BOILER-FLAPFIX v10.6: zon-dump beslist op POTENTIEEL teruglevering
  (zonder boiler) i.p.v. bruto (met boiler), zodat een al-aanstaande
  boiler niet elke 5 min aan/uit flapt door zijn eigen overschot.
- ARBITRAGE-GUARD v10.7: zon-dump-boiler-trigger corrigeert nu voor
  accu-ontlading (sensor.marstek_venus_system_system_discharge_power)
  en wordt bovendien overgeslagen in een actief arbitrage-slot, zodat de
  boiler niet meegesleept wordt door teruglevering die eigenlijk verkoop is.
- STATUS-CACHE v10.8: _status() schrijft input_text alleen bij tekstwijziging,
  plus een "status_sinds" tijdstempel zodat je kunt zien hoelang een
  beslissing al standhoudt. Minder HA-belasting + beter inzicht.
- v10.9 FIXES (na eerste nacht):
  (1) MIN_SOC_HARD 15 -> 12 (BMS heeft eigen diepte-ontladings-beveiliging).
  (2) max_discharge_power minimum is 800W hardware; onder 800W wordt de
      allow_discharge switch gebruikt i.p.v. number.set_value (was: error).
  (3) input_datetime parsing accepteert "YYYY-MM-DD HH:MM:SS" (met spatie).
  (4) Echte-zon-overschot formule gecorrigeerd: gebruikt ruwe L1 i.p.v.
      afgekapt bruto, zodat een al-aanstaande boiler op net-stroom niet
      als "zon-overschot" wordt gezien (vroegere 4:55u 71°C-bug).

"""

import math
from datetime import datetime, timedelta

# Zichtbare versie-tag — wordt geprepended aan alle status-tekst.
SCRIPT_VERSIE = "v10.12"

# ==============================================================================
# CONFIGURATIE & CONSTANTEN
# ==============================================================================

# Accu Hardware & Limieten
BATTERIJ_CAPACITEIT_KWH = 5.12
MAX_LAAD_W = 1250.0
MAX_ONTLAAD_W = 2500.0
MIN_SOC_HARD = 12.0
MAX_SOC_HARD = 95.0

# Baseloads & Reserves (Vloeren)
NACHT_BASELOAD_KWH = 1.3            # 23:00 - 07:00
AVOND_BASELOAD_KWH = 2.0            # 17:00 - 23:00
DAG_BASELOAD_KWH = 0.5              # 07:00 - 17:00
NACHT_BEHOUD_MARGE_SOC = 10.0
ZON_DIRECT_VERBRUIK_KWH = 4.5

# Financien, Rendement & Degradatie
ROUNDTRIP_EFFICIENCY = 0.88
SLIJTAGE_KOSTEN_EUR = 0.025
MIN_PROFIT_MARGIN_EUR = 0.04
HOOFDZEKERING_L1_GRENS_W = 5750.0
GROOTVERBRUIK_DREMPEL_W = 500.0
MIN_DISCHARGE_POWER_W = 800.0       # Hardware-minimum Marstek; onder 800W via switch uitschakelen

# Boiler & Climate Control
BOILER_TEMP_COMFORT = 42.0
BOILER_TEMP_LEGIONELLA = 62.0
BOILER_TEMP_MAX_DUMP = 71.0
MAX_PRIJS_BOILER_EUR = 0.25
MIN_PRIJS_BOILER_VERWARMEN = 0.10   # onder deze prijs mag comfort-verwarming
LEGIONELLA_INTERVAL_UUR = 168
ZONNE_DUMP_OVERSCHOT_W = 1800.0
ZONNE_DUMP_AFVAL_W = 1500.0
BOILER_TEMP_NOOD_ONDER = 34.0

# Boiler Slimme Stekker (LSC)
BOILER_VERMOGEN_W = 1815.0          # Vast vermogen van de LSC boiler stekker

# Veiligheidsmarges (voorheen magic numbers in de logica)
BOILER_GROOTVERBRUIK_BLOKKADE_W = 1500.0   # Boiler mag NIET aan als overige grootverbruiker > dit
LAAD_VEILIGHEIDS_MARGE_W = 500.0           # Marge onder hoofdzekering bij net-laden
TARGET_GRID_DEADBAND_W = 0.5               # Strikte deadband voor 0-op-de-meter target
NUMBER_DEADBAND_W = 2.0                    # Algemene deadband voor number-entiteiten

# Zonne-drempels
ZONNE_OVERSCHOT_MIN_W = 150.0
ZONNE_L1_EXPORT_MIN_W = 50.0

# Schema-freshness
MAX_SCHEMA_LEEFTIJD_UUR = 3.0

# Actie-log
MAX_LOG_ENTRIES = 25                   # Aantal bewaarde log-regels in HELPER_SCHEMA

# ==============================================================================
# ENTITEITEN  (NIET GEWIJZIGD t.o.v. v10.4.1)
# ==============================================================================

# Accu & Net
ENT_SOC = "sensor.marstek_venus_1_battery_soc"
ENT_TARGET_GRID_POWER = "number.marstek_venus_system_pd_target_grid_power"
ENT_MAX_CHARGE_POWER = "number.marstek_venus_1_max_charge_power"
ENT_MAX_DISCHARGE_POWER = "number.marstek_venus_1_max_discharge_power"
ENT_NETTO_VERMOGEN_L1 = "sensor.actueel_netto_vermogen_l1"
ENT_TIBBER_PRICES = "sensor.tibber_kwartierprijzen"
ENT_ACCU_ONTLAAD_W = "sensor.marstek_venus_system_system_discharge_power"

# Marstek Switches
ENT_ALLOW_DISCHARGE = "switch.marstek_venus_1_battery_allow_discharge"
ENT_ALLOW_CHARGE = "switch.marstek_venus_1_battery_allow_charge"

# Zonne-energie
ENT_ZONNE_VERMOGEN_1 = "sensor.growatt_2500"
ENT_ZONNE_VERMOGEN_2 = "sensor.inverter_overkapping"
ENT_ZON_TODAY_REM_1 = "sensor.energy_production_today_remaining"
ENT_ZON_TODAY_REM_2 = "sensor.energy_production_today_remaining_3"
ENT_ZON_NEXT_HOUR_1 = "sensor.energy_next_hour"
ENT_ZON_NEXT_HOUR_2 = "sensor.energy_next_hour_3"
ENT_ZON_TOMORROW_1 = "sensor.energy_production_tomorrow_3"
ENT_ZON_TOMORROW_2 = "sensor.energy_production_tomorrow"

# Boiler & Grootverbruikers
ENT_BOILER_CLIMATE = "climate.regeling_boiler_element"
ENT_BOILER_SWITCH = "switch.lsc_power_plug_eu_incl_power_meter_2"
ENT_BOILER_PLUG_W = "sensor.lsc_power_plug_eu_incl_power_meter_2_vermogen"
ENT_WASMACHINE_W = "sensor.wasmachine"
ENT_DROGER_W = "sensor.droger"
ENT_VAATWASSER_W = "sensor.vaatwasser_vermogen"
ENT_LAST_LEGIONELLA = "input_datetime.laatste_legionella_run"

# Home Assistant Helpers
HELPER_ACTIEF = "input_boolean.marstek_ai_actief"
HELPER_MARGE = "input_number.marstek_zelfconsumptie_marge_w"  # bijv. -70
HELPER_SCHEMA = "sensor.marstek_ai_schema"
HELPER_STATUS = "input_text.marstek_ai_status"


# ==============================================================================
# HULPFUNCTIES (Pyscript-safe)
# ==============================================================================

def _veilig_state_get(entity_id):
    try:
        return state.get(entity_id)
    except Exception:
        return None


def _veilig_getattr(entity_id):
    try:
        return state.getattr(entity_id) or {}
    except Exception:
        return {}


def _num(entity_id, default=0.0):
    val = _veilig_state_get(entity_id)
    if val in (None, "", "unknown", "unavailable"):
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _state(entity_id, default=None):
    val = _veilig_state_get(entity_id)
    if val in (None, "", "unknown", "unavailable"):
        return default
    return val


def _is_on(entity_id):
    return str(_veilig_state_get(entity_id)).lower() == "on"


def _is_climate_on(entity_id):
    """Controleert of een climate entiteit actief staat te verwarmen/koelen (niet off)."""
    st = str(_veilig_state_get(entity_id)).lower()
    return st not in ("off", "none", "unknown", "unavailable")


def _zet_switch(entity_id, gewenste_status_on):
    if _is_on(entity_id) == gewenste_status_on:
        return
    actie = "turn_on" if gewenste_status_on else "turn_off"
    try:
        service.call("switch", actie, entity_id=entity_id)
    except Exception as e:
        log.error(f"[Switch] Fout bij {actie} op {entity_id}: {e}")


def _zet_number(entity_id, gewenste_waarde):
    if gewenste_waarde is None:
        return
    # Marstek max_discharge_power heeft een hardware-minimum van 800W.
    # set_value < 800 geeft "out_of_range" error. Onder 800W wordt ontladen
    # geregeld via de allow_discharge switch, dus hier stilletjes overslaan.
    if entity_id == ENT_MAX_DISCHARGE_POWER and gewenste_waarde < MIN_DISCHARGE_POWER_W:
        return
    huidige = _num(entity_id, -99999.0)
    target = round(float(gewenste_waarde))

    # Slimme deadband: bij streefwaarde 0 strikt controleren, anders 2W tolerantie
    if target == 0:
        if abs(huidige) < TARGET_GRID_DEADBAND_W:
            return
    elif abs(huidige - target) < NUMBER_DEADBAND_W:
        return

    try:
        service.call("number", "set_value", entity_id=entity_id, value=target)
    except Exception as e:
        log.error(f"[Number] Fout bij {entity_id} -> {target}: {e}")


def _stuur_boiler(hvac_mode, target_temp=None, schakel_stekker_aan=False):
    """Stuurt ebusd climate regeling aan ÉN de fysieke LSC slimme stekker."""
    # 1. Fysieke LSC stekker direct schakelen voor harde vermogenssturing
    _zet_switch(ENT_BOILER_SWITCH, schakel_stekker_aan)

    huidige_modus = _state(ENT_BOILER_CLIMATE)

    # 2. Eerst hvac_mode instellen bij inschakelen
    if hvac_mode != "off" and huidige_modus != hvac_mode:
        try:
            service.call("climate", "set_hvac_mode",
                         entity_id=ENT_BOILER_CLIMATE, hvac_mode=hvac_mode)
            huidige_modus = hvac_mode
        except Exception as e:
            log.error(f"[Climate] set_hvac_mode fout: {e}")

    # 3. Pas temperatuur instellen als de modus actief is
    if hvac_mode != "off" and target_temp is not None:
        attrs = _veilig_getattr(ENT_BOILER_CLIMATE)
        huidige_target = float(attrs.get("temperature", 0) or 0)
        if abs(huidige_target - target_temp) >= 0.5:
            try:
                service.call("climate", "set_temperature",
                             entity_id=ENT_BOILER_CLIMATE, temperature=float(target_temp))
            except Exception as e:
                log.error(f"[Climate] set_temperature fout: {e}")

    # 4. Uitschakelen als gewenste modus 'off' is
    if hvac_mode == "off" and huidige_modus != "off":
        try:
            service.call("climate", "set_hvac_mode",
                         entity_id=ENT_BOILER_CLIMATE, hvac_mode="off")
        except Exception as e:
            log.error(f"[Climate] set_hvac_mode off fout: {e}")


# ------------------------------------------------------------------------------
# STATUS & ACTIE-LOG  (NIEUW in v10.5)
# ------------------------------------------------------------------------------
#
# Werking:
#   _status(kort, uitgebreid, details=None)
#     - 'kort'      -> geschreven naar HELPER_STATUS  (1-regel dashboard-label)
#     - 'uitgebreid' -> geschreven naar HELPER_SCHEMA attribute 'uitleg'
#                       (meerregelige mens-leesbare verklaring van WAT + WAAROM)
#     - 'details'    -> optioneel dict met meetwaarden, ook in 'uitleg'
#
#   _log_actie(categorie, actie, reden)
#     - Voegt een tijdstempel-regel toe aan de rolling log (max MAX_LOG_ENTRIES)
#     - Wordt opgeslagen in HELPER_SCHEMA attribute 'actie_log' als list[dict]
#     - In HA af te beelden in een Markdown-card of template sensor
#
# De log blijft bewaard tussen regel-loops in (leest/werkt via HELPER_SCHEMA attrs).

# Module-level cache voor de korte status-tekst. Voorkomt dat _status()
# elke 5 minuten een state.set doet als de tekst niet gewijzigd is.
_LAATSTE_STATUS_TEKST = None
_LAATSTE_STATUS_SINDS = None


def _status(kort, uitgebreid=None, details=None):
    """Stelt de korte status in (input_text) — alleen bij wijziging — en de
    uitgebreide uitleg (attribute op HELPER_SCHEMA). Fouten worden stil gepakt."""
    global _LAATSTE_STATUS_TEKST, _LAATSTE_STATUS_SINDS
    nu = datetime.now().astimezone()
    # Prepend versie-tag zodat je altijd kunt zien welke versie draait.
    kort = f"[{SCRIPT_VERSIE}] {kort}"
    # Korte status alleen schrijven als de tekst écht anders is.
    if kort != _LAATSTE_STATUS_TEKST:
        _LAATSTE_STATUS_TEKST = kort
        _LAATSTE_STATUS_SINDS = nu
        try:
            state.set(HELPER_STATUS, kort)
        except Exception:
            pass
    # "sinds" altijd up-to houden op de actuele entiteit via attribute,
    # zodat het dashboard hoelang-standhoudt kan tonen. Light: alleen schrijven
    # als er al een tekst is (i.e. minimaal één keer geset).
    if _LAATSTE_STATUS_SINDS is not None:
        try:
            attrs = _veilig_getattr(HELPER_STATUS) or {}
            attrs["status_sinds"] = _LAATSTE_STATUS_SINDS.strftime("%Y-%m-%d %H:%M:%S")
            attrs["status_duur_min"] = round((nu - _LAATSTE_STATUS_SINDS).total_seconds() / 60.0, 1)
            state.set(HELPER_STATUS, _LAATSTE_STATUS_TEKST or "", new_attributes=attrs)
        except Exception:
            pass
    if uitgebreid is not None:
        try:
            attrs = _veilig_getattr(HELPER_SCHEMA) or {}
            attrs["uitleg"] = uitgebreid
            if isinstance(details, dict):
                attrs["uitleg_details"] = details
            state.set(HELPER_SCHEMA, value=attrs.get("state", "Actief"),
                      new_attributes=attrs)
        except Exception as e:
            log.warning(f"[Status] Kon uitgebreide uitleg niet opslaan: {e}")


def _log_actie(categorie, actie, reden):
    """Voegt één gecombineerde log-regel toe aan de rolling actie-log.
    Categorie: bv 'ACCu', 'BOILER', 'FAIL-SAFE', 'ZEKERING'.
    Actie:     wat er gebeurt, bv 'Daluren-laden 2000W'.
    Reden:     waarom, bv 'prijs 0.08 < gem 0.21, SoC 42% < max 95%'."""
    try:
        nu = datetime.now().astimezone()
        attrs = _veilig_getattr(HELPER_SCHEMA) or {}
        log_list = list(attrs.get("actie_log", []))
        log_list.append({
            "tijd": nu.strftime("%H:%M:%S"),
            "datum": nu.strftime("%Y-%m-%d"),
            "categorie": categorie,
            "actie": actie,
            "reden": reden,
        })
        # Bewaar alleen de laatste N entries
        if len(log_list) > MAX_LOG_ENTRIES:
            log_list = log_list[-MAX_LOG_ENTRIES:]
        attrs["actie_log"] = log_list
        attrs["laatste_actie"] = f"{nu.strftime('%H:%M:%S')} | {categorie} | {actie}"
        state.set(HELPER_SCHEMA, value=attrs.get("state", "Actief"),
                  new_attributes=attrs)
    except Exception as e:
        log.warning(f"[Log] Kon actie-log niet wegschrijven: {e}")


def _bereken_vloer_soc():
    """Dynamische SOC-vloer op basis van uur van de dag."""
    now = datetime.now().astimezone()  # tz-bewust, was voorheen naakt datetime.now()
    uur = now.hour
    if 17 <= uur < 23:
        vloer_kwh = AVOND_BASELOAD_KWH
    elif uur >= 23 or uur < 7:
        vloer_kwh = NACHT_BASELOAD_KWH
    else:
        vloer_kwh = DAG_BASELOAD_KWH
    baseload_soc_pct = (vloer_kwh / BATTERIJ_CAPACITEIT_KWH) * 100.0
    return min(MAX_SOC_HARD, MIN_SOC_HARD + NACHT_BEHOUD_MARGE_SOC + baseload_soc_pct)


def _live_zon_w():
    return max(0.0, _num(ENT_ZONNE_VERMOGEN_1, 0.0) + _num(ENT_ZONNE_VERMOGEN_2, 0.0))


def _verwachte_zon_morgen_kwh():
    return max(0.0, _num(ENT_ZON_TOMORROW_1, 0.0) + _num(ENT_ZON_TOMORROW_2, 0.0))


def _verwachte_zon_resterend_kwh():
    return max(0.0, _num(ENT_ZON_TODAY_REM_1, 0.0) + _num(ENT_ZON_TODAY_REM_2, 0.0))


def _get_kwartier_key(dt):
    minute = (dt.minute // 15) * 15
    kwartier_dt = dt.replace(minute=minute, second=0, microsecond=0)
    return kwartier_dt.isoformat()


def _parse_tibber_kwartierprijzen():
    attrs = _veilig_getattr(ENT_TIBBER_PRICES)
    if not attrs:
        return []

    raw_list = []
    for key in ("prices", "kwartierprijzen", "today", "prices_today", "tomorrow", "prices_tomorrow"):
        v = attrs.get(key)
        if isinstance(v, list):
            raw_list.extend(v)

    if not raw_list:
        return []

    parsed = {}
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        start_raw = item.get("start") or item.get("startsAt") or item.get("time") or item.get("from")

        price_raw = None
        for pk in ("price", "total", "value", "prijs"):
            if pk in item and item[pk] is not None:
                price_raw = item[pk]
                break
        if start_raw is None or price_raw is None:
            continue
        try:
            start_dt = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00")).astimezone()
            k = _get_kwartier_key(start_dt)
            parsed[k] = {"start": start_dt, "price": float(price_raw), "key": k}
        except (TypeError, ValueError):
            continue

    return sorted(parsed.values(), key=lambda x: x["start"])


def _overig_grootverbruik_w():
    return max(0.0,
               _num(ENT_WASMACHINE_W, 0.0) +
               _num(ENT_DROGER_W, 0.0) +
               _num(ENT_VAATWASSER_W, 0.0))


def _totaal_grootverbruik_w():
    return _overig_grootverbruik_w() + max(0.0, _num(ENT_BOILER_PLUG_W, 0.0))


# ==============================================================================
# 1. AI SCHEMA BEREKENING (Uurlijks, 13:15 uur update & Startup)
# ==============================================================================

@service
@time_trigger("cron(0 * * * *)")
@time_trigger("cron(15 13 * * *)")
@time_trigger("startup")
def marstek_ai_bereken_schema():
    # Bij (her)start van Pyscript: status-cache resetten zodat de eerste
    # _status() weer altijd daadwerkelijk schrijft.
    global _LAATSTE_STATUS_TEKST, _LAATSTE_STATUS_SINDS
    _LAATSTE_STATUS_TEKST = None
    _LAATSTE_STATUS_SINDS = None
    if _state(HELPER_ACTIEF) != "on":
        _status("Inactief - schema niet herberekend",
                "AI inactief (input_boolean.marstek_ai_actief staat UIT).\n"
                "Schema wordt niet herberekend; aansturing ligt stil.")
        return

    kwartieren = _parse_tibber_kwartierprijzen()
    if not kwartieren:
        _status("Geen prijsdata beschikbaar",
                "Tibber-kwartierprijzen niet gevonden op sensor.tibber_kwartierprijzen.\n"
                "Mogelijke oorzaken: integratie offline, attributen leeg, of nog niet opgehaald.\n"
                "Wacht op de volgende 5-min regelloop; accu blijft op laatst bekende aansturing.")
        return

    tzinfo = kwartieren[0]["start"].tzinfo
    now = datetime.now(tzinfo)

    geldige_kwartieren = [k for k in kwartieren if k["start"] >= now - timedelta(minutes=15)]
    if len(geldige_kwartieren) < 2:
        _status("Te weinig toekomstige prijsdata",
                f"Slechts {len(geldige_kwartieren)} toekomstige kwartieren beschikbaar.\n"
                "Te weinig om een zinvol schema te bouwen. Wacht op nieuwe Tibber-data.")
        return

    huidige_soc = _num(ENT_SOC, 50.0)

    zon_resterend_kwh = _verwachte_zon_resterend_kwh()
    zon_morgen_kwh = _verwachte_zon_morgen_kwh()
    ruwe_zon = zon_resterend_kwh + zon_morgen_kwh
    totale_bruikbare_zon = max(0.0, ruwe_zon - ZON_DIRECT_VERBRUIK_KWH)
    zon_soc_pct = (totale_bruikbare_zon / BATTERIJ_CAPACITEIT_KWH) * 100.0 if BATTERIJ_CAPACITEIT_KWH > 0 else 0.0

    vloer_soc = _bereken_vloer_soc()
    effectieve_max_soc_nacht = max(vloer_soc, MAX_SOC_HARD - zon_soc_pct)

    ruimte_net_laden_kwh = max(0.0, BATTERIJ_CAPACITEIT_KWH * (effectieve_max_soc_nacht - huidige_soc) / 100.0)
    kwh_per_kwartier = (MAX_LAAD_W / 1000.0) * 0.25
    nodige_laad_slots = int(math.ceil(ruimte_net_laden_kwh / kwh_per_kwartier)) if kwh_per_kwartier > 0 else 0
    nodige_laad_slots = max(0, min(len(geldige_kwartieren), nodige_laad_slots))

    beschikbare_soc_arbitrage = max(0.0, huidige_soc - vloer_soc)
    ruimte_ontladen_kwh = (beschikbare_soc_arbitrage / 100.0) * BATTERIJ_CAPACITEIT_KWH
    kwh_per_kwartier_ontlaad = (MAX_ONTLAAD_W / 1000.0) * 0.25
    max_ontlaad_slots = int(math.floor(ruimte_ontladen_kwh / kwh_per_kwartier_ontlaad)) if kwh_per_kwartier_ontlaad > 0 else 0
    max_ontlaad_slots = max(0, max_ontlaad_slots)

    prijzen = [k["price"] for k in geldige_kwartieren]
    gemiddelde_prijs = sum(prijzen) / len(prijzen)

    goedkoopste = sorted(geldige_kwartieren, key=lambda k: k["price"])[:nodige_laad_slots]
    laad_set = set([k["key"] for k in goedkoopste])

    if goedkoopste:
        laad_prijzen = [k["price"] for k in goedkoopste]
        werkelijke_inkoopprijs = sum(laad_prijzen) / len(laad_prijzen)
    else:
        werkelijke_inkoopprijs = 0.0

    min_ontlaad_prijs = (werkelijke_inkoopprijs / ROUNDTRIP_EFFICIENCY) + SLIJTAGE_KOSTEN_EUR + MIN_PROFIT_MARGIN_EUR

    duurste = sorted(
        [k for k in geldige_kwartieren if k["price"] >= min_ontlaad_prijs],
        key=lambda k: k["price"], reverse=True
    )[:max_ontlaad_slots]
    ontlaad_set = set([k["key"] for k in duurste])

    schema = []
    for k in geldige_kwartieren:
        # Voorheen <= 0.0; nu < 0.0, zodat exact 0 niet automatisch laden triggert
        is_laad_slot = (k["key"] in laad_set) or (k["price"] < 0.0)
        is_ontlaad_slot = (k["key"] in ontlaad_set) and not is_laad_slot
        if is_laad_slot:
            actie = "laden"
        elif is_ontlaad_slot:
            actie = "ontladen"
        else:
            actie = "idle"
        schema.append({
            "start": k["start"].isoformat(),
            "price": k["price"],
            "actie": actie,
            "is_laad_slot": is_laad_slot,
            "is_ontlaad_slot": is_ontlaad_slot,
            "key": k["key"],
        })

    state.set(
        HELPER_SCHEMA,
        value="Actief",
        new_attributes={
            "schema": schema,
            "generated_at": now.isoformat(),
            "gemiddelde_prijs": round(gemiddelde_prijs, 4),
            "werkelijke_inkoopprijs": round(werkelijke_inkoopprijs, 4),
            "min_ontlaad_prijs": round(min_ontlaad_prijs, 4),
            "vloer_soc": round(vloer_soc, 1),
            "effectieve_max_soc_nacht": round(effectieve_max_soc_nacht, 1),
            "verwachte_zon_morgen_kwh": round(zon_morgen_kwh, 2),
            "verwachte_zon_resterend_kwh": round(zon_resterend_kwh, 2),
            "nodige_laad_slots": nodige_laad_slots,
            "max_ontlaad_slots": max_ontlaad_slots,
            "dynamisch_laad_w": MAX_LAAD_W,
            "uitleg": (
                f"Schema herberekend om {now.strftime('%H:%M')}.\n"
                f"- {nodige_laad_slots} laad-slot(s) gekozen uit {len(geldige_kwartieren)} kwartieren "
                f"(gem. prijs €{gemiddelde_prijs:.3f}, inkoop €{werkelijke_inkoopprijs:.3f}).\n"
                f"- {len(ontlaad_set)} ontlad-slot(s) voor arbitrage, drempel €{min_ontlaad_prijs:.3f} "
                f"(= inkoop/{ROUNDTRIP_EFFICIENCY} + slijtage €{SLIJTAGE_KOSTEN_EUR} + marge €{MIN_PROFIT_MARGIN_EUR}).\n"
                f"- SoC nu {huidige_soc:.0f}%, vloer {vloer_soc:.0f}%, max-nacht {effectieve_max_soc_nacht:.0f}%.\n"
                f"- Verwachte zon: rest vandaag {zon_resterend_kwh:.1f} kWh + morgen {zon_morgen_kwh:.1f} kWh "
                f"(70% bruikbaar = {totale_bruikbare_zon:.1f} kWh)."
            ),
        },
    )
    _status(f"Schema v10.12: {nodige_laad_slots} laad / {len(ontlaad_set)} ontlaad, "
            f"min verkoop €{round(min_ontlaad_prijs,3)}, vloer {round(vloer_soc)}%")
    _log_actie("SCHEMA", f"Nieuw schema: {nodige_laad_slots} laad / {len(ontlaad_set)} ontlaad",
               f"gem. €{gemiddelde_prijs:.3f}, drempel €{min_ontlaad_prijs:.3f}, "
               f"SoC {huidige_soc:.0f}%, vloer {vloer_soc:.0f}%")

    try:
        marstek_ai_regel()
    except Exception as e:
        log.error(f"[Schema] Directe regel-aanroep na schema-update faalde: {e}")


# ==============================================================================
# 2. HOOFD-REGELLOOP (Elke 5 minuten)
# ==============================================================================

@service
@time_trigger("cron(*/5 * * * *)")
def marstek_ai_regel():
    if _state(HELPER_ACTIEF) != "on":
        return

    now = datetime.now().astimezone()
    huidige_kwartier_key = _get_kwartier_key(now)
    huidige_l1_w = _num(ENT_NETTO_VERMOGEN_L1, 0.0)
    huidige_soc = _num(ENT_SOC, 50.0)
    marge_w = _num(HELPER_MARGE, -70.0)

    zonne_opbrengst_w = _live_zon_w()
    # Bruto = wat de meter ziet (negatief = teruglevering).
    # Potentieel = wat de meter zou zien ZONDER de boiler — belangrijk omdat
    # een al-aanstaande boiler zijn eigen zon-overschot opeet op de meter.
    # Beslissingen over "zon-dump" moeten op potentieel gebeuren, anders flapt
    # de boiler elke 5 min aan/uit zodra hij aan staat (L1 = -1500 ipv -3300).
    bruto_teruglevering_w = -huidige_l1_w if huidige_l1_w < 0 else 0.0

    grootverbruik_apparaten_w = _overig_grootverbruik_w()
    boiler_actueel_w = max(0.0, _num(ENT_BOILER_PLUG_W, 0.0))
    # Actuele accu-ontlading uitlezen. In een arbitrage-slot pompt de accu
    # zelf 2500W naar net; die teruglevering is geen zon-overschot en mag
    # de boiler-dump niet triggeren. Fallback 0 als sensor wegvalt.
    accu_ontlaad_w = max(0.0, _num(ENT_ACCU_ONTLAAD_W, 0.0))
    # Echte zon-overschot = wat geëxporteerd zou worden ZONDER boiler EN ZONDER
    # accu-ontlading. Belangrijk: gebruik de RUWE L1 (niet afgekapt bruto),
    # anders telt een al-aanstaande boiler die op net-stroom draait als
    # "zon-overschot" (de 4:55u 71°C-bug: boiler aan -> L1=+1815 -> bruto=0
    # maar oude formule telde boiler 1815 erbij -> fictief overschot).
    #   L1=+1815(import, boiler aan), boiler=1815 -> max(0,-1815+1815)=0  ✓
    #   L1=-1500(export), boiler=1815              -> max(0, 1500+1815)=3315 ✓
    echte_zon_overschot_w = max(0.0, -huidige_l1_w + boiler_actueel_w - accu_ontlaad_w)
    totaal_grootverbruik_w = grootverbruik_apparaten_w + boiler_actueel_w
    is_grootverbruiker_actief = grootverbruik_apparaten_w > GROOTVERBRUIK_DREMPEL_W

    # ---- 1. SCHEMA FRESHNESS CHECK ----
    attrs = _veilig_getattr(HELPER_SCHEMA)
    generated_at_str = attrs.get("generated_at")
    schema_ongeldig = False
    if not generated_at_str:
        schema_ongeldig = True
    else:
        try:
            gen_dt = datetime.fromisoformat(generated_at_str)
            if (now - gen_dt).total_seconds() > (MAX_SCHEMA_LEEFTIJD_UUR * 3600):
                schema_ongeldig = True
        except (TypeError, ValueError):
            schema_ongeldig = True

    if schema_ongeldig:
        log.error("[Fail-Safe] Schema ontbreekt of ouder dan 3u. Veilige standby + herberekenen.")
        _zet_switch(ENT_ALLOW_CHARGE, huidige_soc < MAX_SOC_HARD)
        _zet_switch(ENT_ALLOW_DISCHARGE, huidige_soc > MIN_SOC_HARD)
        _zet_number(ENT_MAX_CHARGE_POWER, MAX_LAAD_W if huidige_soc < MAX_SOC_HARD else 0.0)
        _zet_number(ENT_MAX_DISCHARGE_POWER, MAX_ONTLAAD_W if huidige_soc > MIN_SOC_HARD else 0.0)
        _zet_number(ENT_TARGET_GRID_POWER, marge_w if huidige_soc > MIN_SOC_HARD else 0.0)
        _status("[Fail-Safe] Schema ongeldig - veilige 0-op-de-meter, schema wordt herberekend",
                f"FAIL-SAFE actief om {now.strftime('%H:%M')}.\n"
                f"Schema ontbreekt of is ouder dan {MAX_SCHEMA_LEEFTIJD_UUR:.0f} uur.\n"
                f"WAAROM veilige 0-op-de-meter i.p.v. accu uit?\n"
                f"  - Accu uit = geen zelfconsumptie = onnodig net-verbruik.\n"
                f"  - 0-op-de-meter met discharge vrij laat de accu het huis blijven voeden.\n"
                f"  - Laden blijft vrij voor zon-overschot zolang SoC < {MAX_SOC_HARD}%.\n"
                f"SoC nu {huidige_soc:.0f}%, marge-target {marge_w:.0f}W.\n"
                f"Schema wordt direct herberekend; volgende loop werkt weer normaal.")
        _log_actie("FAIL-SAFE", "Veilige 0-op-de-meter (discharge vrij)",
                   f"schema > {MAX_SCHEMA_LEEFTIJD_UUR:.0f}u oud of ongeldig; SoC {huidige_soc:.0f}%")
        marstek_ai_bereken_schema()
        return

    schema = attrs.get("schema", [])
    vloer_soc = float(attrs.get("vloer_soc", _bereken_vloer_soc()))
    min_ontlaad_prijs_attr = float(attrs.get("min_ontlaad_prijs", 0.0))
    gemiddelde_prijs_attr = float(attrs.get("gemiddelde_prijs", 0.0))

    # ---- 2. KWARTIER-MATCHING ----
    is_laad_slot = False
    is_ontlaad_slot = False
    actuele_prijs = 0.20  # fallback (zie log hieronder)
    slot_gevonden = False
    for kwartier in schema:
        try:
            if kwartier.get("key") == huidige_kwartier_key:
                is_laad_slot = bool(kwartier.get("is_laad_slot", False))
                is_ontlaad_slot = bool(kwartier.get("is_ontlaad_slot", False))
                actuele_prijs = float(kwartier.get("price", 0.20))
                slot_gevonden = True
                break
        except (TypeError, ValueError):
            continue

    if not slot_gevonden:
        log.warning(f"[Regel] Huidig kwartier niet in schema, fallback-prijs €0.20 gebruikt.")
        _log_actie("WARN", "Kwartier niet in schema",
                   f"key={huidige_kwartier_key} niet gevonden; fallback €0.20 gehanteerd")

    # Boiler-attrs voor temperatuur
    boiler_attrs = _veilig_getattr(ENT_BOILER_CLIMATE)
    boiler_temp = float(boiler_attrs.get("current_temperature", 45.0) or 45.0)

    # ---- 3. SOC GUARDS ----
    if huidige_soc <= MIN_SOC_HARD:
        log.warning(f"[Guard] SoC {huidige_soc}% <= min {MIN_SOC_HARD}%. Ontladen gate dicht.")
        _zet_switch(ENT_ALLOW_DISCHARGE, False)
    if huidige_soc >= MAX_SOC_HARD:
        log.info(f"[Guard] SoC {huidige_soc}% >= max {MAX_SOC_HARD}%. Laden gate dicht.")
        _zet_switch(ENT_ALLOW_CHARGE, False)

    # ---- 4. THERMISCHE BOILER REGELING (Inclusief LSC Stekker) ----
    legionella_nodig = False
    last_str = _state(ENT_LAST_LEGIONELLA)
    if last_str not in (None, "", "unknown", "unavailable"):
        dt_last = None
        # Poging 1: input_datetime attribute "timestamp" (Unix epoch) — meest robuust.
        try:
            l_attrs = _veilig_getattr(ENT_LAST_LEGIONELLA) or {}
            ts_attr = l_attrs.get("timestamp")
            if ts_attr is not None:
                dt_last = datetime.fromtimestamp(float(ts_attr)).astimezone()
                log.info(f"[Legionella] Poging 1 geslaagd via timestamp-attr: {ts_attr} -> {dt_last}")
            else:
                log.info("[Legionella] Poging 1: geen timestamp-attribute gevonden")
        except (TypeError, ValueError, OSError) as e:
            log.warning(f"[Legionella] Poging 1 faalde: {e}")
            dt_last = None
        # Poging 2: state-string parsen ("2026-07-24 21:35:00" of ISO "T").
        if dt_last is None:
            try:
                dt_last = datetime.fromisoformat(str(last_str).replace(" ", "T"))
                # CRUCIAAL: fromisoformat geeft een tz-NAIVE datetime, terwijl
                # now tz-aware is. (now - dt_last) zou anders een TypeError
                # geven ("offset-naive and offset-aware"). .astimezone() maakt
                # dt_last tz-aware onder de lokale tijdzone (zelfde als now).
                if dt_last.tzinfo is None:
                    dt_last = dt_last.astimezone()
            except (TypeError, ValueError):
                log.warning(f"[Legionella] kon datum niet parsen: '{last_str}'")
                dt_last = None
        # Vergelijk pas als we een geldige dt_last hebben.
        if dt_last is not None and (now - dt_last).total_seconds() > (LEGIONELLA_INTERVAL_UUR * 3600):
            legionella_nodig = True
    else:
        legionella_nodig = False

    boiler_moet_aan = False
    boiler_target = BOILER_TEMP_COMFORT
    boiler_reden = ""

    # Zone C: Zonne-dump of negatieve prijs.
    # Beslist op POTENTIEEL (zonder boiler), niet op bruto (met boiler).
    # Zo blijft een al-aanstaande boiler aan zolang er echt zon-overschot is,
    # in plaats van elke 5 min aan/uit te flappen omdat hij zijn eigen
    # overschot op de meter wegtrekt.
    # Extra guard: in een actief arbitrage-slot wordt de zon-dump-trigger
    # overgeslagen — verkopen heeft daar prioriteit boven boiler-dump.
    arbitrage_actief = is_ontlaad_slot and not is_laad_slot
    if (not arbitrage_actief and echte_zon_overschot_w >= ZONNE_DUMP_OVERSCHOT_W) or actuele_prijs < 0.0:
        boiler_moet_aan, boiler_target = True, BOILER_TEMP_MAX_DUMP
        boiler_reden = (f"zon-dump (echte overschot {echte_zon_overschot_w:.0f}W = bruto {bruto_teruglevering_w:.0f}W "
                        f"+ boiler {boiler_actueel_w:.0f}W - accu-ontlaad {accu_ontlaad_w:.0f}W >= {ZONNE_DUMP_OVERSCHOT_W}W) "
                        f"of negatieve prijs (€{actuele_prijs:.3f}); verwarm tot {BOILER_TEMP_MAX_DUMP}°C als thermische buffer")
    # Zone C hysteresis: boiler mag aan blijven zolang we niet méér dan een
    # kleine marge importeren. Voorkomt flappen bij windstil overschot rond
    # de drempel — beslist op bruto (wat de meter écht ziet) zodat we niet
    # onnodig blijven importeren om een al-warme boiler te voeden.
    elif _is_climate_on(ENT_BOILER_CLIMATE) and boiler_temp < BOILER_TEMP_MAX_DUMP \
         and bruto_teruglevering_w >= ZONNE_DUMP_AFVAL_W:
        boiler_moet_aan, boiler_target = True, BOILER_TEMP_MAX_DUMP
        boiler_reden = (f"hysteresis: boiler al aan en nog {bruto_teruglevering_w:.0f}W bruto export "
                        f">= {ZONNE_DUMP_AFVAL_W}W; blijf dumpen tot {BOILER_TEMP_MAX_DUMP}°C "
                        f"(boiler {boiler_temp:.1f}°C < max {BOILER_TEMP_MAX_DUMP}°C)")
    # Legionella of laad-slot bij lage prijs
    elif legionella_nodig or (is_laad_slot and actuele_prijs <= MIN_PRIJS_BOILER_VERWARMEN):
        if grootverbruik_apparaten_w < BOILER_GROOTVERBRUIK_BLOKKADE_W:
            boiler_moet_aan = True
            boiler_target = BOILER_TEMP_LEGIONELLA if legionella_nodig else BOILER_TEMP_COMFORT
            if legionella_nodig:
                boiler_reden = (f"legionella-cycle nodig (laatste > {LEGIONELLA_INTERVAL_UUR}u geleden); "
                                f"verwarm tot {BOILER_TEMP_LEGIONELLA}°C, grootverbruik {grootverbruik_apparaten_w:.0f}W "
                                f"< {BOILER_GROOTVERBRUIK_BLOKKADE_W}W dus veilig")
            else:
                boiler_reden = (f"laad-slot met lage prijs €{actuele_prijs:.3f} <= €{MIN_PRIJS_BOILER_VERWARMEN}; "
                                f"verwarm boiler voordelig mee tot {BOILER_TEMP_COMFORT}°C")
        else:
            boiler_reden = (f"boiler zou aan (legionella/laagprijs) maar geblokkeerd: "
                            f"grootverbruik {grootverbruik_apparaten_w:.0f}W >= {BOILER_GROOTVERBRUIK_BLOKKADE_W}W; "
                            f"wacht tot grootverbruiker klaar is")
    # Nood-bodem
    elif boiler_temp < BOILER_TEMP_NOOD_ONDER:
        boiler_moet_aan, boiler_target = True, BOILER_TEMP_COMFORT
        boiler_reden = (f"nood-bodem: boiler {boiler_temp:.1f}°C < {BOILER_TEMP_NOOD_ONDER}°C; "
                        f"verwarm tot {BOILER_TEMP_COMFORT}°C ongeacht prijs (sanitair comfort)")
    else:
        boiler_reden = (f"boiler uit: prijs €{actuele_prijs:.3f} > €{MAX_PRIJS_BOILER_EUR} of "
                        f"target {boiler_target:.0f}°C bereikt (nu {boiler_temp:.1f}°C); "
                        f"wacht op zon-dump of goedkoper kwartier")

    if boiler_moet_aan:
        _stuur_boiler("heat", boiler_target, schakel_stekker_aan=True)
        _log_actie("BOILER", f"AAN -> {boiler_target:.0f}°C", boiler_reden)
        if legionella_nodig and boiler_temp >= (BOILER_TEMP_LEGIONELLA - 2.0):
            try:
                service.call("input_datetime", "set_datetime",
                             entity_id=ENT_LAST_LEGIONELLA,
                             datetime=now.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception as e:
                log.error(f"[Legionella] Fout bij opslaan timestamp: {e}")
    elif actuele_prijs > MAX_PRIJS_BOILER_EUR or boiler_temp >= boiler_target:
        _stuur_boiler("off", schakel_stekker_aan=False)
    else:
        _stuur_boiler("off", schakel_stekker_aan=False)

    # ---- 5. HOOFDZEKERING BEVEILIGING ----
    if huidige_l1_w > HOOFDZEKERING_L1_GRENS_W:
        _zet_switch(ENT_ALLOW_DISCHARGE, True)
        _zet_switch(ENT_ALLOW_CHARGE, False)
        _zet_number(ENT_MAX_CHARGE_POWER, 0.0)
        _zet_number(ENT_MAX_DISCHARGE_POWER, MAX_ONTLAAD_W)
        _zet_number(ENT_TARGET_GRID_POWER, -MAX_ONTLAAD_W)
        _stuur_boiler("off", schakel_stekker_aan=False)
        _status(f"NOOD-ASSISTENTIE: L1 {round(huidige_l1_w)}W > zekering!",
                f"NOOD-INTERVENTIE om {now.strftime('%H:%M')}.\n"
                f"L1-vermogen {huidige_l1_w:.0f}W > hoofdzekering {HOOFDZEKERING_L1_GRENS_W:.0f}W.\n"
                f"Actie:\n"
                f"  - Accu ontladen op {MAX_ONTLAAD_W:.0f}W om piek af te vlakken.\n"
                f"  - Laden geblokkeerd (anders erger).\n"
                f"  - Boiler uit om extra belasting te voorkomen.\n"
                f"Prioriteit: zekering beschermen boven financieel rendement.")
        _log_actie("ZEKERING", f"NOOD ontladen {MAX_ONTLAAD_W:.0f}W",
                   f"L1 {huidige_l1_w:.0f}W > {HOOFDZEKERING_L1_GRENS_W:.0f}W; boiler uit, laden uit")
        return

    # ---- 6. ACCU-AANSTURING (drievoudig) ----
    if is_laad_slot and huidige_soc < MAX_SOC_HARD:
        # LAAD-SLOT
        _zet_switch(ENT_ALLOW_DISCHARGE, False)
        _zet_switch(ENT_ALLOW_CHARGE, True)
        _zet_number(ENT_MAX_DISCHARGE_POWER, 0.0)

        # Veilig laadvermogen: hoofdzekering - grootverbruik - marge
        beschikbaar_laad_w = max(0.0, HOOFDZEKERING_L1_GRENS_W - totaal_grootverbruik_w - LAAD_VEILIGHEIDS_MARGE_W)
        effectief_laad_w = min(MAX_LAAD_W, beschikbaar_laad_w)

        _zet_number(ENT_MAX_CHARGE_POWER, effectief_laad_w)
        _zet_number(ENT_TARGET_GRID_POWER, effectief_laad_w)
        _status(f"Daluren: net-laden {round(effectief_laad_w)}W (prijs {round(actuele_prijs,3)}, SoC {round(huidige_soc)}%)",
                f"DALUREN-LADEN om {now.strftime('%H:%M')}.\n"
                f"WAAROM: huidig kwartier is een laad-slot (prijs €{actuele_prijs:.3f} behoort tot goedkoopste).\n"
                f"- Doel-SoC {effectieve_max_soc_nacht_attr:.0f}% (vloer {vloer_soc:.0f}% + zon-reserve), nu {huidige_soc:.0f}%.\n"
                f"- Laadvermogen {effectief_laad_w:.0f}W = min(MAX {MAX_LAAD_W:.0f}W, "
                f"zekering {HOOFDZEKERING_L1_GRENS_W:.0f} - grootverbruik {totaal_grootverbruik_w:.0f} - marge {LAAD_VEILIGHEIDS_MARGE_W}).\n"
                f"- Ontladen geblokkeerd; zon-overschot komt later gratis bovenop.".replace(
                    "{effectieve_max_soc_nacht_attr}", str(attrs.get('effectieve_max_soc_nacht', '?'))))
        _log_actie("ACCU", f"Laden {effectief_laad_w:.0f}W uit net",
                   f"prijs €{actuele_prijs:.3f}, SoC {huidige_soc:.0f}%, grootverbruik {totaal_grootverbruik_w:.0f}W")

    elif is_laad_slot and huidige_soc >= MAX_SOC_HARD:
        # Laad-slot maar accu vol
        _zet_switch(ENT_ALLOW_DISCHARGE, False)
        _zet_switch(ENT_ALLOW_CHARGE, False)
        _zet_number(ENT_MAX_CHARGE_POWER, 0.0)
        _zet_number(ENT_MAX_DISCHARGE_POWER, 0.0)
        _zet_number(ENT_TARGET_GRID_POWER, 0.0)
        _status(f"Daluren: accu vol (SoC {round(huidige_soc)}%)",
                f"LAAD-SLOT MAAR ACCU VOL om {now.strftime('%H:%M')}.\n"
                f"WAAROM: prijs €{actuele_prijs:.3f} is laag, maar SoC {huidige_soc:.0f}% >= max {MAX_SOC_HARD:.0f}%.\n"
                f"- Geen ruimte om te laden (cell-degradatiebescherming).\n"
                f"- Niet ontladen (geen arbitrage-slot, prijs te laag).\n"
                f"- Accu in neutrale stand; zon mag later wel bijladen tot max.\n"
                f"Wacht op duurder kwartier om te ontladen, of op zon-dump voor boiler.")
        _log_actie("ACCU", "Laden overgeslagen",
                   f"laad-slot maar SoC {huidige_soc:.0f}% >= max {MAX_SOC_HARD:.0f}%")

    elif is_ontlaad_slot and huidige_soc > vloer_soc and not is_grootverbruiker_actief:
        # ARBITRAGE-SLOT: actief ontladen naar net
        _zet_switch(ENT_ALLOW_DISCHARGE, True)
        _zet_switch(ENT_ALLOW_CHARGE, False)
        _zet_number(ENT_MAX_CHARGE_POWER, 0.0)
        _zet_number(ENT_MAX_DISCHARGE_POWER, MAX_ONTLAAD_W)
        _zet_number(ENT_TARGET_GRID_POWER, -MAX_ONTLAAD_W)
        winst_per_kwh = actuele_prijs - min_ontlaad_prijs_attr
        _status(f"Arbitrage: -{round(MAX_ONTLAAD_W)}W naar net (prijs {round(actuele_prijs,3)} > {round(min_ontlaad_prijs_attr,3)})",
                f"ARBITRAGE-ONTLADEN om {now.strftime('%H:%M')}.\n"
                f"WAAROM: huidige prijs €{actuele_prijs:.3f} >= drempel €{min_ontlaad_prijs_attr:.3f}.\n"
                f"- Drempel = inkoop €{attrs.get('werkelijke_inkoopprijs', 0):.3f} / efficiency {ROUNDTRIP_EFFICIENCY} "
                f"+ slijtage €{SLIJTAGE_KOSTEN_EUR} + marge €{MIN_PROFIT_MARGIN_EUR}.\n"
                f"- Geschatte winst ≈ €{winst_per_kwh:.3f}/kWh.\n"
                f"- SoC {huidige_soc:.0f}% > vloer {vloer_soc:.0f}% (reserve voor avond/nacht bewaard).\n"
                f"- Geen grootverbruiker actief ({grootverbruik_apparaten_w:.0f}W < {GROOTVERBRUIK_DREMPEL_W}W).\n"
                f"Accu levert {MAX_ONTLAAD_W:.0f}W aan net via target_grid_power = -{MAX_ONTLAAD_W:.0f}.")
        _log_actie("ACCU", f"Arbitrage -{MAX_ONTLAAD_W:.0f}W naar net",
                   f"prijs €{actuele_prijs:.3f} >= drempel €{min_ontlaad_prijs_attr:.3f}, winst ≈ €{winst_per_kwh:.3f}/kWh")

    elif zonne_opbrengst_w > ZONNE_OVERSCHOT_MIN_W and huidige_l1_w < -ZONNE_L1_EXPORT_MIN_W and huidige_soc < MAX_SOC_HARD:
        # ZONNE-OVERSCHOT: laad uit zon
        _zet_switch(ENT_ALLOW_DISCHARGE, False)
        _zet_switch(ENT_ALLOW_CHARGE, True)
        _zet_number(ENT_MAX_CHARGE_POWER, MAX_LAAD_W)
        _zet_number(ENT_MAX_DISCHARGE_POWER, 0.0)
        _zet_number(ENT_TARGET_GRID_POWER, 0.0)
        _status(f"Zon-overschot: accu laden ({round(zonne_opbrengst_w)}W), P1=0",
                f"ZON-OVERSCHOT-LADEN om {now.strftime('%H:%M')}.\n"
                f"WAAROM: zon levert {zonne_opbrengst_w:.0f}W en P1 toont {bruto_teruglevering_w:.0f}W teruglevering.\n"
                f"- Gratis energie: liever in accu dan terugleveren (terugleververgoeding vaak laag/nul).\n"
                f"- SoC {huidige_soc:.0f}% < max {MAX_SOC_HARD:.0f}%, dus er is ruimte.\n"
                f"- target_grid_power = 0: accu slurpt overschot op, P1 rond neutraal.\n"
                f"- Boiler (indien nodig) wordt parallel via zon-dump bijgevuld.\n"
                f"Verwachte resterende zon vandaag: {attrs.get('verwachte_zon_resterend_kwh', '?')} kWh.")
        _log_actie("ACCU", f"Zon-laden {zonne_opbrengst_w:.0f}W",
                   f"P1 teruglevering {bruto_teruglevering_w:.0f}W, SoC {huidige_soc:.0f}%, gratis energie")

    elif huidige_soc > MIN_SOC_HARD:
        # STANDBY / 0-OP-DE-METER
        _zet_switch(ENT_ALLOW_DISCHARGE, True)
        _zet_switch(ENT_ALLOW_CHARGE, huidige_soc < MAX_SOC_HARD)
        _zet_number(ENT_MAX_CHARGE_POWER, MAX_LAAD_W)
        _zet_number(ENT_MAX_DISCHARGE_POWER, MAX_ONTLAAD_W)
        _zet_number(ENT_TARGET_GRID_POWER, marge_w)

        if is_grootverbruiker_actief and is_ontlaad_slot:
            _status(f"0-op-de-meter: arbitrage geblokkeerd door grootverbruiker ({round(grootverbruik_apparaten_w)}W)",
                    f"0-OP-DE-METER (arbitrage geblokkeerd) om {now.strftime('%H:%M')}.\n"
                    f"WAAROM geen arbitrage ondanks gunstige prijs €{actuele_prijs:.3f}?\n"
                    f"- Grootverbruiker actief: {grootverbruik_apparaten_w:.0f}W > drempel {GROOTVERBRUIK_DREMPEL_W}W.\n"
                    f"- Actief ontladen zou de L1-piek verhogen i.p.v. verlagen.\n"
                    f"- Accu blijft daarom op 0-op-de-meter: huis wordt uit accu gevoed, P1 ≈ {marge_w:.0f}W.\n"
                    f"- Zodra grootverbruiker klaar is, heroverweegt de volgende loop arbitrage.\n"
                    f"SoC {huidige_soc:.0f}%, vloer {vloer_soc:.0f}%.")
            _log_actie("ACCU", "0-op-de-meter (arbitrage geblokkeerd)",
                       f"grootverbruik {grootverbruik_apparaten_w:.0f}W > {GROOTVERBRUIK_DREMPEL_W}W; prijs €{actuele_prijs:.3f}")
        elif huidige_soc <= vloer_soc:
            _status(f"0-op-de-meter: vloer-SOC actief ({round(huidige_soc)}% <= {round(vloer_soc)}%)",
                    f"0-OP-DE-METER (vloer-SOC) om {now.strftime('%H:%M')}.\n"
                    f"WAAROM geen arbitrage ondanks gunstige prijs €{actuele_prijs:.3f}?\n"
                    f"- SoC {huidige_soc:.0f}% <= vloer {vloer_soc:.0f}%.\n"
                    f"- Vloer = reserve voor avond-/nacht-baseload (voorkomt diep-ontladen).\n"
                    f"- Accu mag nog wel zelfconsumptie leveren (discharge vrij), maar niet actief naar net pompen.\n"
                    f"- target_grid_power = {marge_w:.0f}W houdt P1 net boven nul.\n"
                    f"Wacht op laad-slot (goedkoper kwartier) om reserve weer aan te vullen.")
            _log_actie("ACCU", "0-op-de-meter (vloer-SOC)",
                       f"SoC {huidige_soc:.0f}% <= vloer {vloer_soc:.0f}%; reserve bewaard")
        else:
            resterende_zon = attrs.get('verwachte_zon_resterend_kwh', '?')
            _status(f"0-op-de-meter: P1-target {round(marge_w)}W (SoC {round(huidige_soc)}%, prijs {round(actuele_prijs,3)})",
                    f"0-OP-DE-METER om {now.strftime('%H:%M')}.\n"
                    f"WAAROM: geen actief laad- of arbitrage-slot.\n"
                    f"- Prijs €{actuele_prijs:.3f} ligt tussen drempels (gem. €{gemiddelde_prijs_attr:.3f}, "
                    f"min-ontlaad €{min_ontlaad_prijs_attr:.3f}).\n"
                    f"- SoC {huidige_soc:.0f}% binnen veiligheidsmarge (vloer {vloer_soc:.0f}%, max {MAX_SOC_HARD:.0f}%).\n"
                    f"- Accu doet zelfconsumptie: huis uit accu, target P1 ≈ {marge_w:.0f}W.\n"
                    f"- Boiler later: wacht op zon-dump of laag-prijs-kwartier.\n"
                    f"- Resterende zon vandaag: {resterende_zon} kWh (wordt bewaard voor avond/nacht).\n"
                    f"Discharge vrij, charge vrij voor eventueel zon-overschot.")
            _log_actie("ACCU", f"0-op-de-meter (P1≈{marge_w:.0f}W)",
                       f"prijs €{actuele_prijs:.3f} tussen drempels, SoC {huidige_soc:.0f}%, zelfconsumptie")

    else:
        # ACCU LEEG
        _zet_switch(ENT_ALLOW_DISCHARGE, False)
        _zet_switch(ENT_ALLOW_CHARGE, huidige_soc < MAX_SOC_HARD)
        _zet_number(ENT_MAX_CHARGE_POWER, MAX_LAAD_W)
        _zet_number(ENT_MAX_DISCHARGE_POWER, 0.0)
        _zet_number(ENT_TARGET_GRID_POWER, 0.0)
        _status(f"Standby: accu leeg ({round(huidige_soc)}% <= {round(MIN_SOC_HARD)}%)",
                f"ACCU BESCHERMD om {now.strftime('%H:%M')}.\n"
                f"WAAROM: SoC {huidige_soc:.0f}% <= min {MIN_SOC_HARD:.0f}% (cell-bescherming).\n"
                f"- Ontladen volledig geblokkeerd (gate dicht, max-discharge 0).\n"
                f"- Huis wordt volledig uit net gevoerd; accu ligt stil.\n"
                f"- Laden blijft vrij voor zon-overschot zodra dat beschikbaar is.\n"
                f"Wacht op laad-slot of zon om SoC weer op te bouwen.")
        _log_actie("ACCU", "Accu leeg - ontladen geblokkeerd",
                   f"SoC {huidige_soc:.0f}% <= min {MIN_SOC_HARD:.0f}%; celbescherming actief")
