"""
Trello Weekly Snapshot — skrypt dla GitHub Actions
Odpytuje Trello API i dopisuje wiersz do history.json
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta

TRELLO_KEY   = os.environ['TRELLO_KEY']
TRELLO_TOKEN = os.environ['TRELLO_TOKEN']
BOARD_ID     = os.environ['BOARD_ID']
WAIT_LIST_ID = os.environ['WAIT_LIST_ID']

BASE = 'https://api.trello.com/1'
AUTH = {'key': TRELLO_KEY, 'token': TRELLO_TOKEN}


def get_week_bounds():
    """Zwraca poniedziałek 00:00 i niedzielę 23:59 bieżącego tygodnia (UTC)."""
    now  = datetime.now(timezone.utc)
    diff = now.weekday()  # 0 = poniedziałek
    mon  = (now - timedelta(days=diff)).replace(hour=0,  minute=0,  second=0,  microsecond=0)
    sun  = (now - timedelta(days=diff)).replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(days=6)
    return mon, sun


def trello_get(path, extra=None):
    params = {**AUTH, **(extra or {})}
    r = requests.get(f'{BASE}{path}', params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    mon, sun = get_week_bounds()
    now = datetime.now(timezone.utc)

    print(f"Snapshot: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Tydzień:  {mon.strftime('%d.%m')}–{sun.strftime('%d.%m.%Y')}")

    # 1. Karty w poczekalni
    wait_cards = trello_get(f'/lists/{WAIT_LIST_ID}/cards', {'fields': 'id,name,due'})
    print(f"Poczekalnia: {len(wait_cards)} kart")

    # 2. Wszystkie otwarte karty na tablicy
    all_cards = trello_get(f'/boards/{BOARD_ID}/cards', {
        'filter': 'open', 'fields': 'id,name,due,idList,dueComplete'
    })

    # 3. Wadia z terminem w bieżącym tygodniu
    week_cards = [
        c for c in all_cards
        if c.get('due') and not c.get('dueComplete')
        and mon.isoformat() <= c['due'] <= sun.isoformat()
    ]
    print(f"Ten tydzień: {len(week_cards)} wadiów z TERMINEM")

    # 4. Akcje tablicy — dla nowych kart i czasu oczekiwania
    actions = trello_get(f'/boards/{BOARD_ID}/actions', {
        'filter': 'createCard,updateCard:idList',
        'limit':  '1000',
        'fields': 'data,date,type'
    })

    # Nowe karty w tym tygodniu (tylko te które pojawiły się na poczekalni)
    new_this_week = [
        a for a in actions
        if a['type'] == 'createCard'
        and a.get('data', {}).get('list', {}).get('id') == WAIT_LIST_ID
        and mon.isoformat() <= a['date'] <= sun.isoformat()
    ]
    print(f"Nowe w tym tygodniu: {len(new_this_week)}")

    # Średni czas oczekiwania bieżących kart w poczekalni
    wait_ids = {c['id'] for c in wait_cards}
    arrival_map = {}
    for a in actions:
        cid = a.get('data', {}).get('card', {}).get('id')
        if not cid or cid not in wait_ids or cid in arrival_map:
            continue
        if a['type'] == 'createCard' and a.get('data', {}).get('list', {}).get('id') == WAIT_LIST_ID:
            arrival_map[cid] = a['date']
        elif a['type'] == 'updateCard' and a.get('data', {}).get('listAfter', {}).get('id') == WAIT_LIST_ID:
            arrival_map[cid] = a['date']

    hours_list = []
    for c in wait_cards:
        if c['id'] in arrival_map:
            arr = datetime.fromisoformat(arrival_map[c['id']].replace('Z', '+00:00'))
            hours_list.append((now - arr).total_seconds() / 3600)
    avg_hours = sum(hours_list) / len(hours_list) if hours_list else None
    print(f"Śr. czas w poczekalni: {round(avg_hours, 1)}h" if avg_hours else "Śr. czas: brak danych")

    # Średnie wyprzedzenie nowych kart (dni od wpłynięcia do TERMINU)
    card_due_map = {c['id']: c.get('due') for c in all_cards if c.get('due')}
    lead_days = []
    for a in new_this_week:
        cid = a.get('data', {}).get('card', {}).get('id')
        if cid and cid in card_due_map:
            created = datetime.fromisoformat(a['date'].replace('Z', '+00:00'))
            due     = datetime.fromisoformat(card_due_map[cid].replace('Z', '+00:00'))
            days = (due - created).total_seconds() / 86400
            if days > 0:
                lead_days.append(days)
    avg_lead = sum(lead_days) / len(lead_days) if lead_days else None

    # Buduj snapshot (ten sam format co localStorage w HTML)
    snapshot = {
        'ts':          now.isoformat(),
        'waiting':     len(wait_cards),
        'thisWeek':    len(week_cards),
        'avgHours':    avg_hours,
        'newThisWeek': len(new_this_week),
        'avgLeadDays': avg_lead
    }

    # Wczytaj istniejącą historię
    history_path = 'history.json'
    try:
        with open(history_path, 'r', encoding='utf-8') as f:
            history = json.load(f)
        if not isinstance(history, list):
            history = []
    except (FileNotFoundError, json.JSONDecodeError):
        history = []

    # Dopisz nowy snapshot (najnowszy na początku, jak w localStorage)
    history = [snapshot] + history
    history = history[:104]   # przechowuj max 2 lata tygodniowych snapshotów

    with open(history_path, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    print(f"Zapisano history.json ({len(history)} snapshotów)")


if __name__ == '__main__':
    main()
