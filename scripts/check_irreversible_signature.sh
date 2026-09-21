#!/usr/bin/env bash
# §3.4a: объявленное необратимое требует подписи оператора.
#
# ⚠ ВТОРАЯ реализация, и область у неё УЖЕ первой. Первая
# (`delivery_runtime.unsigned_irreversible_gaps`) едет с payload'ом, имеет
# детектор поверхностей и потому решает САМА, перейдена ли черта: нет
# поверхности — молчит. Здесь детектора нет, и решение принято раньше —
# самой установкой этого шага в CI. Поэтому отсутствие объявления тут
# красное, а там тихое, и это не расхождение, а разные юрисдикции.
#
# Где обе судят одно и то же — объявление ЕСТЬ, — согласие держит
# дифференциальный оракул в сьюте канона (`one-notion-one-place`: две
# реализации одной формы разъезжаются на первой правке).
#
# Что проверяется: подпись СХОДИТСЯ С СОДЕРЖИМЫМ объявления. Правка строки
# после подписи её ломает — иначе агент подписал бы безобидное и дописал
# остальное.
#
# ⚠ Скрипт умеет только `verify` и НЕ умеет `sign`. Это не упущение: подпись
# запрашивает человек, читая объявление. Запрос, который может вызвать агент,
# превращается в рефлекс подтверждения — замерено 21.09.2026, четыре касания
# подряд без знания о подписываемом.
set -uo pipefail

STATUS="delivery/active/STATUS.md"
SIG="delivery/active/irreversible.sig"
SIGNERS=".github/allowed_signers"
NS="irreversible"

line=$(grep -m1 -E '^\s*-\s+\*\*irreversible_surfaces:\*\*' "$STATUS" 2>/dev/null \
       | sed -E 's/^.*\*\*irreversible_surfaces:\*\*[[:space:]]*//')

if [ -z "$line" ]; then
  echo "НЕТ строки irreversible_surfaces: в $STATUS (§3.4a)."
  echo "Назови поверхности, до которых агент дотягивается без человека,"
  echo "либо 'none reason=…'. Молчание объявлением не считается."
  exit 1
fi

if printf '%s' "$line" | grep -qiE '^(none|n/a)\b'; then
  if printf '%s' "$line" | grep -qiE 'reason[[:space:]]*=[[:space:]]*[^[:space:]]'; then
    echo "irreversible_surfaces: отказ с причиной — подпись не требуется."
    exit 0
  fi
  echo "'none' БЕЗ reason= объявлением не считается (§3.4a)."
  exit 1
fi

for f in "$SIG" "$SIGNERS"; do
  [ -f "$f" ] || { echo "нет $f — объявлено необратимое, подписи нет (§3.4a)"; exit 1; }
done

who=$(awk 'NF {print $1; exit}' "$SIGNERS")
if printf '%s' "$line" | ssh-keygen -Y verify -f "$SIGNERS" -I "$who" -n "$NS" -s "$SIG" >/dev/null 2>&1; then
  echo "подпись сходится с объявлением: $line"
  exit 0
fi

echo "ПОДПИСЬ НЕ СХОДИТСЯ с объявлением (§3.4a):"
echo "  $line"
echo
echo "Это остановка, а не повод просить waiver: сначала escalation.md с двумя"
echo "вариантами и ценой — поставить человека в цепочку или подписать."
echo "Подписывает ЧЕЛОВЕК, прочитав строку:"
echo "  SOCK=\$(find ~/Library/Containers -name socket.ssh 2>/dev/null | head -1)"
echo "  printf '%s' '<строка объявления>' | SSH_AUTH_SOCK=\$SOCK \\"
echo "    ssh-keygen -Y sign -f <публичный ключ> -n irreversible - > $SIG"
exit 1
