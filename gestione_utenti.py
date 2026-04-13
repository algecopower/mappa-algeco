#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Gestione Utenti - Script per creare, modificare ed eliminare utenti.

Uso:
  python gestione_utenti.py                   # Menu interattivo
  python gestione_utenti.py --crea NOME       # Crea utente con password casuale
  python gestione_utenti.py --crea NOME --password PASS  # Crea con password specifica
  python gestione_utenti.py --lista           # Lista tutti gli utenti
  python gestione_utenti.py --elimina NOME    # Elimina utente
  python gestione_utenti.py --reset NOME      # Reset password con nuova casuale
  python gestione_utenti.py --toggle NOME     # Attiva/Disattiva utente
  python gestione_utenti.py --bulk N          # Crea N utenti in bulk
  python gestione_utenti.py --province NOME TORINO,PAVIA  # Imposta province utente
"""
import sys
import os
import argparse

# Aggiungi directory corrente al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from moduli.auth import (
    create_user,
    delete_user,
    reset_password,
    list_users,
    toggle_user_active,
    generate_password,
    get_user_province,
    set_user_province,
    set_user_role,
    PROVINCE_DISPONIBILI,
    RUOLI_DISPONIBILI
)


def stampa_intestazione():
    """Stampa intestazione script."""
    print("\n" + "=" * 60)
    print("  GESTIONE UTENTI - Gestionale Mappa Contatori")
    print("=" * 60)


def stampa_utenti(utenti):
    """Stampa lista utenti in formato tabella."""
    if not utenti:
        print("\n📭 Nessun utente registrato.")
        return
    
    print(f"\n{'Username':<20} {'Attivo':<8} {'Ruolo':<8} {'Province':<20} {'Ultimo login':<20}")
    print("-" * 78)
    
    for u in utenti:
        stato = "✅" if u.get('active', True) else "❌"
        ruolo = u.get('ruolo', 'user')
        province = ', '.join(u.get('province', ['TUTTE']))
        ultimo = u.get('last_login') or 'Mai'
        if ultimo and ultimo != 'Mai':
            # Formatta data
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(ultimo)
                ultimo = dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass
        
        print(f"{u['username']:<20} {stato:<8} {ruolo:<8} {province:<20} {ultimo:<20}")
    
    print(f"\nTotale: {len(utenti)} utenti")


def crea_utente_interattivo():
    """Crea un utente in modo interattivo."""
    print("\n📝 CREA NUOVO UTENTE")
    print("-" * 40)
    
    username = input("Username: ").strip()
    if not username:
        print("❌ Username vuoto!")
        return
    
    # Chiedi se generare password
    scelta = input("Generare password casuale? [S/n]: ").strip().lower()
    
    if scelta == 'n':
        password = input("Password: ").strip()
        if len(password) < 8:
            print("⚠️ Password troppo corta (min 8 caratteri)!")
            return
    else:
        password = None  # Genera automaticamente
    
    # Crea utente
    success, msg, generated_pass = create_user(username, password)
    
    if success:
        print(f"\n✅ {msg}")
        if generated_pass:
            print(f"\n🔐 PASSWORD GENERATA: {generated_pass}")
            print("   ⚠️ Salva questa password, non sarà più visibile!")
    else:
        print(f"\n❌ {msg}")


def crea_utenti_bulk():
    """Crea più utenti in bulk."""
    print("\n📦 CREAZIONE BULK UTENTI")
    print("-" * 40)
    
    try:
        n = int(input("Quanti utenti creare? "))
        if n < 1 or n > 100:
            print("❌ Numero non valido (1-100)")
            return
    except ValueError:
        print("❌ Inserisci un numero valido")
        return
    
    prefisso = input("Prefisso username (default: 'utente'): ").strip() or 'utente'
    
    print("\n📋 Utenti creati:")
    print("-" * 60)
    print(f"{'Username':<20} {'Password':<30}")
    print("-" * 60)
    
    utenti_creati = []
    
    for i in range(1, n + 1):
        username = f"{prefisso}{i}"
        success, msg, generated_pass = create_user(username, None, 'user')
        
        if success:
            print(f"{username:<20} {generated_pass:<30}")
            utenti_creati.append((username, generated_pass))
        else:
            print(f"{username:<20} ❌ {msg}")
    
    print("-" * 60)
    print(f"\n✅ Creati {len(utenti_creati)}/{n} utenti")
    
    # Salva in file
    if utenti_creati:
        salva = input("\nSalvare credenziali in file? [S/n]: ").strip().lower()
        if salva != 'n':
            filename = f"credenziali_utenti_{prefisso}.txt"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write("CREDENZIALI UTENTI\n")
                f.write("=" * 60 + "\n\n")
                for username, password in utenti_creati:
                    f.write(f"Username: {username}\n")
                    f.write(f"Password: {password}\n")
                    f.write("-" * 30 + "\n")
            print(f"✅ Credenziali salvate in: {filename}")


def elimina_utente_interattivo():
    """Elimina un utente in modo interattivo."""
    print("\n🗑️ ELIMINA UTENTE")
    print("-" * 40)
    
    # Mostra utenti esistenti
    utenti = list_users()
    if not utenti:
        print("📭 Nessun utente da eliminare.")
        return
    
    stampa_utenti(utenti)
    
    username = input("\nUsername da eliminare: ").strip()
    if not username:
        print("❌ Username vuoto!")
        return
    
    conferma = input(f"⚠️ Confermi eliminazione di '{username}'? [s/N]: ").strip().lower()
    if conferma != 's':
        print("Operazione annullata.")
        return
    
    success, msg = delete_user(username)
    
    if success:
        print(f"\n✅ {msg}")
    else:
        print(f"\n❌ {msg}")


def reset_password_interattivo():
    """Reset password in modo interattivo."""
    print("\n🔄 RESET PASSWORD")
    print("-" * 40)
    
    # Mostra utenti esistenti
    utenti = list_users()
    if not utenti:
        print("📭 Nessun utente registrato.")
        return
    
    stampa_utenti(utenti)
    
    username = input("\nUsername: ").strip()
    if not username:
        print("❌ Username vuoto!")
        return
    
    success, msg, new_pass = reset_password(username)
    
    if success:
        print(f"\n✅ {msg}")
        print(f"\n🔐 NUOVA PASSWORD: {new_pass}")
        print("   ⚠️ Salva questa password, non sarà più visibile!")
    else:
        print(f"\n❌ {msg}")


def toggle_utente_interattivo():
    """Attiva/Disattiva utente in modo interattivo."""
    print("\n🔄 ATTIVA/DISATTIVA UTENTE")
    print("-" * 40)
    
    # Mostra utenti esistenti
    utenti = list_users()
    if not utenti:
        print("📭 Nessun utente registrato.")
        return
    
    stampa_utenti(utenti)
    
    username = input("\nUsername: ").strip()
    if not username:
        print("❌ Username vuoto!")
        return
    
    success, msg, new_state = toggle_user_active(username)
    
    if success:
        stato = "attivo" if new_state else "disabilitato"
        print(f"\n✅ Utente '{username}' ora è {stato}")
    else:
        print(f"\n❌ {msg}")


def gestisci_province_interattivo():
    """Gestisce le province accessibili per un utente."""
    print("\n🗺️  GESTIONE PROVINCE UTENTE")
    print("-" * 40)
    
    # Mostra utenti esistenti
    utenti = list_users()
    if not utenti:
        print("📭 Nessun utente registrato.")
        return
    
    stampa_utenti(utenti)
    
    username = input("\nUsername: ").strip()
    if not username:
        print("❌ Username vuoto!")
        return
    
    province_attuali = get_user_province(username)
    if not province_attuali and province_attuali != []:
        print(f"❌ Utente '{username}' non trovato")
        return
    
    print(f"\nProvince attuali di '{username}': {', '.join(province_attuali) if province_attuali else 'nessuna'}")
    print(f"Province disponibili: {', '.join(PROVINCE_DISPONIBILI)}")
    print()
    
    for i, prov in enumerate(PROVINCE_DISPONIBILI, 1):
        stato = "✅" if prov in province_attuali else "❌"
        print(f"  {i}. {prov} {stato}")
    
    print()
    print("Inserisci le province separate da virgola (es: TORINO,PAVIA)")
    print("oppure 'tutte' per assegnare tutte le province:")
    
    scelta = input("\nProvince: ").strip().upper()
    
    if scelta == 'TUTTE':
        nuove_province = list(PROVINCE_DISPONIBILI)
    else:
        nuove_province = [p.strip() for p in scelta.split(',') if p.strip()]
    
    if not nuove_province:
        print("❌ Nessuna provincia specificata!")
        return
    
    success, msg = set_user_province(username, nuove_province)
    
    if success:
        print(f"\n✅ {msg}")
    else:
        print(f"\n❌ {msg}")


def menu_principale():
    """Menu principale interattivo."""
    while True:
        stampa_intestazione()
        
        print("\nOperazioni disponibili:")
        print("  1. 📋 Lista utenti")
        print("  2. ➕ Crea nuovo utente")
        print("  3. 📦 Crea utenti in bulk")
        print("  4. 🗑️  Elimina utente")
        print("  5. 🔄 Reset password")
        print("  6. 🔒 Attiva/Disattiva utente")
        print("  7. 🗺️  Gestione province")
        print("  0. 🚪 Esci")
        
        scelta = input("\nScelta: ").strip()
        
        if scelta == '1':
            utenti = list_users()
            stampa_utenti(utenti)
            input("\nPremi INVIO per continuare...")
        
        elif scelta == '2':
            crea_utente_interattivo()
            input("\nPremi INVIO per continuare...")
        
        elif scelta == '3':
            crea_utenti_bulk()
            input("\nPremi INVIO per continuare...")
        
        elif scelta == '4':
            elimina_utente_interattivo()
            input("\nPremi INVIO per continuare...")
        
        elif scelta == '5':
            reset_password_interattivo()
            input("\nPremi INVIO per continuare...")
        
        elif scelta == '6':
            toggle_utente_interattivo()
            input("\nPremi INVIO per continuare...")
        
        elif scelta == '7':
            gestisci_province_interattivo()
            input("\nPremi INVIO per continuare...")
        
        elif scelta == '0':
            print("\n👋 Arrivederci!\n")
            break
        
        else:
            print("\n❌ Scelta non valida!")
            input("Premi INVIO per continuare...")


def main():
    """Entry point principale."""
    parser = argparse.ArgumentParser(description='Gestione Utenti')
    parser.add_argument('--crea', '-c', metavar='USERNAME', help='Crea nuovo utente')
    parser.add_argument('--password', '-p', metavar='PASSWORD', help='Password (con --crea)')
    parser.add_argument('--lista', '-l', action='store_true', help='Lista tutti gli utenti')
    parser.add_argument('--elimina', '-e', metavar='USERNAME', help='Elimina utente')
    parser.add_argument('--reset', metavar='USERNAME', help='Reset password utente')
    parser.add_argument('--toggle', '-t', metavar='USERNAME', help='Attiva/Disattiva utente')
    parser.add_argument('--bulk', '-b', type=int, metavar='N', help='Crea N utenti in bulk')
    parser.add_argument('--province', nargs=2, metavar=('USERNAME', 'PROVINCE'),
                        help='Imposta province utente (es: --province giada TORINO,PAVIA)')
    
    args = parser.parse_args()
    
    # Se nessun argomento, menu interattivo
    if len(sys.argv) == 1:
        menu_principale()
        return
    
    stampa_intestazione()
    
    # Lista utenti
    if args.lista:
        utenti = list_users()
        stampa_utenti(utenti)
        return
    
    # Crea utente
    if args.crea:
        success, msg, generated_pass = create_user(args.crea, args.password)
        if success:
            print(f"\n✅ {msg}")
            if generated_pass:
                print(f"\n🔐 PASSWORD GENERATA: {generated_pass}")
                print("   ⚠️ Salva questa password, non sarà più visibile!")
        else:
            print(f"\n❌ {msg}")
        return
    
    # Elimina utente
    if args.elimina:
        success, msg = delete_user(args.elimina)
        if success:
            print(f"\n✅ {msg}")
        else:
            print(f"\n❌ {msg}")
        return
    
    # Reset password
    if args.reset:
        success, msg, new_pass = reset_password(args.reset)
        if success:
            print(f"\n✅ {msg}")
            print(f"\n🔐 NUOVA PASSWORD: {new_pass}")
        else:
            print(f"\n❌ {msg}")
        return
    
    # Toggle utente
    if args.toggle:
        success, msg, new_state = toggle_user_active(args.toggle)
        if success:
            stato = "attivo" if new_state else "disabilitato"
            print(f"\n✅ Utente '{args.toggle}' ora è {stato}")
        else:
            print(f"\n❌ {msg}")
        return
    
    # Gestione province
    if args.province:
        username, province_str = args.province
        province = [p.strip() for p in province_str.split(',')]
        success, msg = set_user_province(username, province)
        if success:
            print(f"\n✅ {msg}")
        else:
            print(f"\n❌ {msg}")
        return
    
    # Bulk create
    if args.bulk:
        n = args.bulk
        if n < 1 or n > 100:
            print("❌ Numero non valido (1-100)")
            return
        
        print(f"\n📦 Creazione {n} utenti...")
        print("-" * 60)
        print(f"{'Username':<20} {'Password':<30}")
        print("-" * 60)
        
        for i in range(1, n + 1):
            username = f"utente{i}"
            success, msg, generated_pass = create_user(username, None, 'user')
            
            if success:
                print(f"{username:<20} {generated_pass:<30}")
            else:
                print(f"{username:<20} ❌ {msg}")
        
        print("-" * 60)
        return


if __name__ == '__main__':
    main()
