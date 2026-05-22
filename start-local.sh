#!/bin/bash
# Avvia il relay CarlinGomme in locale — legge le credenziali dal .env.local del progetto
# Uso: ./start-local.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="/Users/gianmarcos/Downloads/work_F5_5_final/.env.local"

if [ ! -f "$ENV_FILE" ]; then
  echo "❌ File .env.local non trovato: $ENV_FILE"
  exit 1
fi

# Leggi le variabili d'ambiente dal .env.local
SUPABASE_URL=$(grep "^NEXT_PUBLIC_SUPABASE_URL=" "$ENV_FILE" | cut -d'=' -f2-)
SUPABASE_KEY=$(grep "^SUPABASE_SERVICE_ROLE_KEY=" "$ENV_FILE" | cut -d'=' -f2-)

if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_KEY" ]; then
  echo "❌ NEXT_PUBLIC_SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY mancanti in .env.local"
  exit 1
fi

export SUPABASE_URL="$SUPABASE_URL"
export SUPABASE_KEY="$SUPABASE_KEY"
export PORT=3781
export FLASK_ENV=production

echo "✅ Relay CarlinGomme in avvio su http://localhost:3781"
echo "   Supabase: $SUPABASE_URL"
echo "   [Ctrl+C per fermare]"
echo ""

cd "$SCRIPT_DIR"
python3 main.py
