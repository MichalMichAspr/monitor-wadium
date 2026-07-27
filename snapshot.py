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
    now  = datetime.now(timezone.utc)
    diff = now.weekday()  # 0 = poniedziałek
    mon  = (now - timedelta(days=diff)).replace(hour=0,  minute=0,  second=0,  microsecond=0)
    sun  = mon + timedelta(days=6, hours=23, minutes=59, seconds=59)
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

    # 3. Wadia z terminem w bieżącym tygodniu — bez filtra dueComplete (jak w JS)
    week_cards = [
        c for c in all_cards
        if c.get('due')
        and mon.isoformat() <= c['due'] <= sun.isoformat()
    ]
    print(f"Ten tydzień: {len(week_cards)} wadiów z TERMINEM")

    # 4. Akcje createCard od poniedziałku — parametr since eliminuje problem limitu 1000
    create_actions = trello_get(f'/boards/{BOARD_ID}/actions', {
        'filter': 'createCard',
        'since':  mon.isoformat(),
        'limit':  '1000',
        'fields': 'data,date,type'
    })

    # 5. Akcje mieszane — dla czasu oczekiwania w poczekalni
    actions = trello_get(f'/boards/{BOARD_ID}/actions', {
        'filter': 'createCard,updateCard:idList',
        'limit':  '1000',
        'fields': 'data,date,type'
    })

    # 6. Listy tablicy — żeby znaleźć ID list roboczych (karty API)
    board_lists = trello_get(f'/boards/{BOARD_ID}/lists', {
        'filter': 'open', 'fields': 'id,name'
    })
    wniosek_list_ids = [
        l['id'] for l in board_lists
        if ('wnios' in l['name'].lower() or 'po analizie' in l['name'].lower())
        and 'zaakceptowane' not in l['name'].lower()
        and 'podpisany' not in l['name'].lower()
    ]

    # Nowe w tym tygodniu = karty ręczne na poczekalni + karty API (proxy)
    # Karty ręczne z poczekalni są kasowane → ich createCard znika z historii Trello
    # Karty API tworzone przez generator = dowód że istniała odpowiadająca karta ręczna
    cr_on_wait  = [a for a in create_actions
                   if a.get('data', {}).get('list', {}).get('id') == WAIT_LIST_ID]
    cr_api_proxy = [a for a in create_actions
                    if a.get('data', {}).get('list', {}).get('id') in wniosek_list_ids]
    new_this_week = cr_on_wait + cr_api_proxy
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

    # Buduj snapshot
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

    history = [snapshot] + history
    history = history[:104]

    with open(history_path, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    print(f"Zapisano history.json ({len(history)} snapshotów)")


if __name__ == '__main__':
    main()
