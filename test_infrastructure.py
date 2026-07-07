#!/usr/bin/env python3
"""
Test infrastructure setup for ARDD-TP.
This script verifies that the basic infrastructure files are in place.
"""

import os
import sys
from pathlib import Path

def check_file_exists(path, description):
    """Check if a file exists and print status."""
    exists = os.path.exists(path)
    status = "✓" if exists else "✗"
    print(f"{status} {description}: {path}")
    return exists

def main():
    print("Checking ARDD-TP infrastructure setup...")
    print("=" * 50)
    
    required_files = [
        ("docker-compose.yml", "Docker Compose configuration"),
        (".env.example", "Environment variables template"),
        ("ingest-gateway/Dockerfile", "Ingest Gateway Dockerfile"),
        ("ingest-gateway/requirements.txt", "Ingest Gateway Python dependencies"),
        ("ingest-gateway/main.py", "Ingest Gateway main application"),
        ("prepare_test_dataset.py", "Test dataset preparation script"),
    ]
    
    all_good = True
    for file_path, description in required_files:
        if not check_file_exists(file_path, description):
            all_good = False
    
    print("\n" + "=" * 50)
    
    # Check directory structure
    print("\nChecking directory structure...")
    required_dirs = [
        "ingest-gateway",
        "PLAN"
    ]
    
    for dir_path in required_dirs:
        exists = os.path.isdir(dir_path)
        status = "✓" if exists else "✗"
        print(f"{status} Directory: {dir_path}/")
        if not exists:
            all_good = False
    
    # Check PLAN directory contents
    print("\nChecking PLAN documentation...")
    plan_files = [
        "PLAN/PHASES.md",
        "PLAN/ARCHITECTURE.md",
        "PLAN/FLOW.md",
        "PLAN/TRD.md",
        "PLAN/SCHEMA.md",
        "PLAN/API_SPEC.md",
        "PLAN/ERROR_HANDLING.md",
        "PLAN/SECURITY.md",
        "PLAN/TESTING.md",
        "PLAN/ROADMAP.md"
    ]
    
    plan_count = 0
    for plan_file in plan_files:
        if os.path.exists(plan_file):
            plan_count += 1
    
    print(f"✓ PLAN documentation: {plan_count}/10 files present")
    
    print("\n" + "=" * 50)
    
    if all_good:
        print("\n✅ Infrastructure setup looks good!")
        print("\nNext steps:")
        print("1. Copy .env.example to .env and fill in your secrets")
        print("2. Run: python prepare_test_dataset.py")
        print("3. Start the stack: docker compose up -d")
        print("4. Test ingest gateway with a video source")
    else:
        print("\n❌ Some infrastructure files are missing.")
        print("Please check the missing items above.")
        sys.exit(1)

if __name__ == "__main__":
    main()