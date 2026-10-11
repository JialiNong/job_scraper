#!/usr/bin/env python3
"""
Generate bcrypt password hash for the job tracker web UI.
Usage: python scripts/generate_password_hash.py
"""
import sys
import getpass
import bcrypt


def main():
    print("=" * 60)
    print("Job Tracker - Password Hash Generator")
    print("=" * 60)
    print()
    print("This will generate a bcrypt hash for your web UI password.")
    print("Add the hash to your .env file as TRACKER_PASSWORD_HASH")
    print()
    
    while True:
        password = getpass.getpass("Enter password: ")
        if not password:
            print("Password cannot be empty. Try again.")
            continue
        
        if len(password) < 6:
            print("Password should be at least 6 characters. Try again.")
            continue
        
        password_confirm = getpass.getpass("Confirm password: ")
        
        if password != password_confirm:
            print("Passwords don't match. Try again.\n")
            continue
        
        break
    
    print("\nGenerating hash...")
    salt = bcrypt.gensalt(rounds=12)
    password_hash = bcrypt.hashpw(password.encode('utf-8'), salt)
    hash_string = password_hash.decode('utf-8')
    
    print("\n" + "=" * 60)
    print("SUCCESS! Add this to your .env file:")
    print("=" * 60)
    print()
    print(f"TRACKER_PASSWORD_HASH={hash_string}")
    print()
    print("=" * 60)
    print("Keep this hash secret and never commit it to git!")
    print("=" * 60)
    

if __name__ == "__main__":
    main()
