# Alarms & Reminders for Home Assistant

Voice alarms and reminders for Assist voice devices (ESPHome satellites, Voice PE, Echo Dots running EchoLocal, and others). Ask your assistant to set as many as you like, one time or repeating, and manage them from the Home Assistant UI too.

- **Alarms:** "Set an alarm for 7 AM" sets a one-time alarm. "Wake me up at 7 every weekday" sets a recurring one. The reply always says which it is and which room it will ring in.
- **Reminders:** "Remind me to call the vet on Friday at 3" is spoken on the voice device and sent to your phone. If you don't give a time, it asks.
- **Wake-up alarms:** volume and lights ease up over 10 minutes, with lights going to full brightness, white.
- **Skip without deleting:** "Skip tomorrow's alarm".
- **Stop and snooze:** "Okay Nabu, stop" or "snooze" while it rings. It stops by itself after 10 minutes.
- **Your own sounds:** a built-in car-alarm siren, beeps and a chime, plus any audio file you upload or link.

Everything runs locally in Home Assistant. Alarms are stored by Home Assistant itself, so they survive restarts.

## How to install

1. Open **Settings › Marketplace** (called **HACS** before HA 2026.11).
2. Open the **⋮** menu and choose **Custom repositories**.
3. Paste `https://github.com/dalekcoffee/HA-Alarms-Reminders`, set the type to **Integration**, and click **Add**.
4. Search for **Alarms & Reminders** and click **Download**.
5. Restart Home Assistant (**Settings › System › ⋮ › Restart**).
6. Go to **Settings › Devices & services › Add Integration**, search for **Alarms & Reminders**, and click **Submit**.

<details>
<summary>Installing without the Marketplace</summary>

Copy `custom_components/alarms_reminders` into `/config/custom_components/` (the **File editor** or **Samba** app works), restart, then do step 6.
</details>

Needs Home Assistant 2026.10 or later.

## Set it up

Open the integration and click **Configure › Settings**:

| Setting | Default | What it does |
|---|---|---|
| Default alarm sound | Car alarm | Used when an alarm doesn't pick its own. |
| Default voice device | none | Where alarms ring when set from a dashboard or the Assist dialog. |
| Ring for | 10 min | Same as Google: it stops by itself after this. |
| Snooze for | 9 min | |
| Alarm volume | 70% | Volume while ringing. Your normal volume comes back afterwards. |
| Wake-up starting volume / ramp | 10%, 10 min | Wake-up alarms start quiet and get louder. |
| Wake-up lights | the room's lights | Leave empty to use the lights in the same room as the voice device. |
| Wake-up light ramp / brightness / colour | 10 min, 100%, 6500 K | Lights go from 1% to full, white. |
| Phones for reminders | none | Pick your `notify.mobile_app_…` action from the Home Assistant app. |

### Sounds

**Configure › Add a sound** lets you upload an audio file (MP3, WAV, OGG, FLAC, M4A) or paste a direct link to one. Sounds are saved under **Media › Local Media › alarms_reminders**.

YouTube and other video pages can't be linked. Downloading from YouTube now needs a JavaScript runtime that Home Assistant OS doesn't include, so it would fail most of the time. Download the audio on your computer and upload it instead.

### Voice

The tools are offered to your conversation agent (Claude, OpenAI, Ollama, and so on) automatically. Make sure the agent's **Control Home Assistant** setting is set to **Assist**.

For "stop" and "snooze" to work instantly without the LLM, turn on **Prefer handling commands locally** in your voice assistant.

Try:
- "Set an alarm for 6:30" → one time
- "Wake me up at 7 every weekday" → recurring wake-up alarm
- "Set an alarm for 9 every weekend in the bedroom"
- "Remind me to take the bins out every Sunday at 8 PM"
- "Remind me to call the vet on Friday" → "What time Friday should I remind you?"
- "What alarms do I have?"
- "Change my 7 AM alarm to 7:15"
- "Skip tomorrow's alarm"
- "Turn off my gym alarm" (keeps it, just switched off) / "Delete my gym alarm"

### In the UI

Each alarm and reminder is listed on the integration page. Use **⋮ › Edit** to change the time, days, room, sound or lights, or to skip the next one. **Add alarm** and **Add reminder** create new ones. Each also has an on/off switch.

You also get **Next alarm** and **Next reminder** sensors, an **Alarm ringing** sensor, and **Stop alarm** / **Snooze alarm** buttons.

### Dashboard card

```yaml
type: vertical-stack
cards:
  - type: markdown
    title: Alarms & reminders
    content: >
      {% for kind in ['alarm', 'reminder'] %}
      {% for i in state_attr('sensor.alarms_reminders_next_' ~ kind, 'items') or [] %}
      {{ '🔔' if kind == 'alarm' else '📝' }} **{{ i.title }}**{{ ' (off)' if not i.enabled }}{{ ' · ' ~ i.room if i.room }}{{ ' · skipping ' ~ i.skipped | join(', ') if i.skipped }}
      {% endfor %}{% endfor %}
  - type: horizontal-stack
    cards:
      - type: button
        entity: button.alarms_reminders_stop_alarm
        tap_action: {action: toggle}
      - type: button
        entity: button.alarms_reminders_snooze_alarm
        tap_action: {action: toggle}
```

To turn alarms on and off from the dashboard, add an **Entities** card with their switches.

### Automations

Events you can trigger on: `alarms_reminders_ringing`, `alarms_reminders_stopped` (with `reason`: stopped, snoozed or timeout) and `alarms_reminders_reminder`. Actions: `alarms_reminders.set_alarm`, `set_reminder`, `skip_next`, `delete`, `list`, `stop`, `snooze`.

## Good to know

- **Stopping on Echo Dots:** you need the wake word first ("Okay Nabu, stop"). The Dot may not hear you over the alarm itself, so there's a 2-second gap between repeats of the sound.
- **Missed alarms:** a one-time alarm that should have rung while Home Assistant was off still rings if it's no more than 10 minutes late. Otherwise it's removed and logged.
- **Cloud lights** (like Govee cloud) update every 30 seconds during the ramp, so they stay within rate limits.
