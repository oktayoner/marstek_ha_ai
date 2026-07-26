Hier is een nette, professionele en heldere `README.md` voor je GitHub-repository. Ook al kijkt er misschien nooit iemand naar, een strakke documentatie is altijd fijn als je over een paar maanden zelf wilt weten hoe het ook alweer zat!

---

# 🤖 Marstek AI Scheduler for Home Assistant

Een geavanceerd Pyscript-automatiseringstramwerk voor Home Assistant om **Marstek Venus** accusystemen intelligent aan te sturen op basis van dynamische uurtarieven (Tibber), zonne-energieprognoses, thermische opslag (boiler) en netwerkbeveiliging.

---

## ✨ Belangrijkste Functionaliteiten

* **Slimme Arbitrage (Tibber):** Berekent op basis van de actieve kwartierprijzen en verwachte zonne-opbrengst automatisch de optimaal goedkope laadslots en dure ontlaadslots.
* **Zon-Overschot Detectie:** Vangt direct overtollige zonne-energie op in de accu of stuurt deze door naar de boiler voordat het het net op gaat.
* **Slimme Boiler & Thermisch Beheer:** Regelt de elektrische boiler automatisch op basis van gratis/goedkope stroom, zonne-dumps en houdt rekening met legionella-intervallen en minimale bodemtemperaturen.
* **Hoofdzekering & Systeem Veiligheid:** Bewaakt het vermogen op fase L1 om overbelasting te voorkomen en hanteert harde SoC-grenzen (State of Charge).
* **Uitgebreid Logboek & Status-tracking:** Houdt per beslismoment een gedetailleerde "waarom-uitleg" bij en slaat een rolling actie-log op voor directe weergave op je dashboard.

---

## 📋 Vereisten & Benodigdheden

* Home Assistant met de **Pyscript** Python scripting integratie.
* Een Marstek Venus accusysteem gekoppeld via Home Assistant.
* Een dynamisch energiecontract (zoals Tibber) met kwartierprijzen.
* Sensoren voor actuele zonne-opbrengst en opbrengstprognoses (bijv. Forecast.Solar of Growatt).

---

## 🚀 Installatie & Configuratie

1. Zorg dat de **Pyscript** integratie correct is geïnstalleerd in Home Assistant.
2. Plaats het script (`marstek_ai.py`) in je `pyscript/` map (bijv. `/config/pyscript/marstek_ai.py`).
3. Controleer en pas eventueel de entiteitnamen aan in het configuratieblok bovenaan het script om te zorgen dat ze overeenkomen met jouw Home Assistant entiteiten (`sensor.marstek_venus_1_battery_soc`, Tibber sensoren, etc.).
4. Herlaad Pyscript via de Home Assistant Developer Tools of herstart je instantie.

---

## 💻 Lovelace Dashboard Voorbeeld

Voeg een **Markdown-kaart** toe aan je dashboard om de actuele status, de motivatie van de laatste beslissing en het recente logboek direct af te lezen:

```yaml
type: markdown
title: "🤖 Marstek AI Scheduler"
content: >
  ### 📊 Actuele Status
  `{{ states('input_text.marstek_ai_status') }}`

  ### 💡 Waarom deze actie?
  {{ state_attr('sensor.marstek_ai_schema', 'uitleg') }}

  ---

  ### 📜 Recente Acties
  {% for entry in state_attr('sensor.marstek_ai_schema', 'actie_log')[-5:] | reverse %}
  - **`{{ entry.tijd }}`** — ⚡ *{{ entry.categorie }}*
    > {{ entry.actie }} — {{ entry.reden }}
  {% endfor %}

```
