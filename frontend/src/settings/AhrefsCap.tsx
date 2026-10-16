/**
 * Ключ Ahrefs и наш кап на экране «Расход»: какого размера ключ и когда спросили, цикл
 * и обнуление, сколько потратили мы и сколько выбрано на ключе всего, сколько осталось, что
 * задано у нас — и «Изменить кап» у того, кому это право выдано.
 *
 * **Кап — от ключа** (решение владельца 16.10.2026: «сервис должен владеть текущей ситуацией
 * по ключу»). Размер ключа и дату обнуления называет сам Ahrefs; на экране кап можно только
 * сузить («не больше N»), а по умолчанию он — весь лимит ключа и следует за ключом. До этого
 * кап жил в окружении сервера и считался с первого числа, а ключ обнуляется в свой день: кап
 * показывал почти выбранным, когда у ключа оставалась большая часть.
 *
 * **Два периода на одном экране — подписаны оба**: блок капа — цикл ключа («с 21 октября»),
 * статьи и плитки под ним — календарный месяц. Иначе два числа Ahrefs читались бы одним.
 *
 * **Экран называет, а не считает**: «осталось» — до того, во что упрёмся раньше (наш кап, лимит
 * ключа или пространства), и доля — числами сервера, тем же правилом, что у тревог сторожа.
 *
 * **Право — «править пороги»**, то же, что у порогов. Без него — только чтение и строка,
 * у кого это право: отсутствие права объясняется на месте (правило экранов).
 */

import { Button, Group, Stack, Text } from '@mantine/core';
import { useState } from 'react';

import { permissionTitle } from '../api/labels';
import type { AhrefsKey, CapBinding } from '../api/settings';
import { useSession } from '../auth/AuthProvider';
import { Meter } from '../components/Meter';
import { formatDateTime, formatDay, formatNumber, formatTime } from '../format';
import { AhrefsCapModal, changedBy } from './AhrefsCapModal';

/** Во что упрёмся раньше — словами строки «осталось». */
const BINDING: Record<CapBinding, string> = {
  cap: 'упирается в наш кап',
  key: 'упирается в лимит ключа',
  workspace: 'упирается в лимит пространства Ahrefs',
};

/** «проверено в 14:05» сегодня, «проверено 15.10.2026, 14:05» — раньше: старый снимок виден. */
function checked(askedAt: string): string {
  const asked = new Date(askedAt);
  return asked.toDateString() === new Date().toDateString()
    ? `проверено в ${formatTime(askedAt)}`
    : `проверено ${formatDateTime(askedAt)}`;
}

/** Размер ключа и когда спросили; ключ без своего лимита — словами, что действует. */
function keyLine(key: AhrefsKey): string {
  const when = key.asked_at === null ? '' : ` · ${checked(key.asked_at)}`;
  if (key.size === null) return `${key.unknown ?? 'Размер ключа Ahrefs неизвестен'}${when}`;
  const what = key.own_limit
    ? `${formatNumber(key.size)} юнитов за цикл`
    : `своего лимита нет — действует лимит пространства, ${formatNumber(key.size)} юнитов за цикл`;
  return `Ключ Ahrefs: ${what}${when}`;
}

/** Цикл капа: от обнуления ключа, а без его даты — календарный месяц, и сказано почему. */
function cycleLine(key: AhrefsKey): string {
  return key.by_key
    ? `Цикл ключа: с ${formatDay(key.cycle_start)}, обнуление ${formatDay(key.next_reset)}`
    : `Цикл — календарный месяц, с ${formatDay(key.cycle_start)} до ${formatDay(key.next_reset)}: Ahrefs не назвал даты обнуления ключа`;
}

/** Потрачено нами и выбрано всего — на ключе или, без своего лимита, в пространстве. */
function spentLine(key: AhrefsKey): string {
  const ours = `Потрачено нами за цикл: ${formatNumber(key.spent_by_us)}`;
  if (key.size === null) return ours;
  if (key.own_limit && key.key_used !== null) {
    return `${ours} · выбрано на ключе всего ${formatNumber(key.key_used)} — вместе с соседней системой на общем ключе`;
  }
  if (key.workspace_used === null) return ours;
  return `${ours} · выбрано в пространстве Ahrefs всего ${formatNumber(key.workspace_used)} — всеми его ключами`;
}

/** Что задано у нас: весь лимит ключа, «не больше N» — или N выше ключа, и действует ключ. */
function chosenLine(key: AhrefsKey): string {
  if (key.chosen === null) return 'Наш кап: весь лимит ключа';
  if (key.above_key && key.size !== null) {
    return `Наш кап: не больше ${formatNumber(key.chosen)} — выше лимита ключа ${formatNumber(key.size)}, действует ${formatNumber(key.size)}`;
  }
  return `Наш кап: не больше ${formatNumber(key.chosen)} юнитов за цикл`;
}

/** Кто и когда поставил то, что задано; смен нет — весь лимит ключа по умолчанию. */
function changedLine(key: AhrefsKey): string {
  const [last] = key.changes;
  return last === undefined
    ? 'Кап на экране ещё не меняли — весь лимит ключа'
    : `Кап изменён: ${changedBy(last)}`;
}

export function AhrefsCap({ ahrefsKey: key }: { ahrefsKey: AhrefsKey }) {
  const { can } = useSession();
  const [editing, setEditing] = useState(false);
  return (
    <Stack gap={6}>
      <Text size="sm" fw={500} style={{ overflowWrap: 'anywhere' }}>
        {keyLine(key)}
      </Text>
      <Text size="sm" c="dimmed">
        {cycleLine(key)}
      </Text>
      <Text size="sm">{spentLine(key)}</Text>
      {key.cap === null ? (
        <Text size="sm" c="dimmed">
          Кап неизвестен, и прогоны не запустятся, пока Ahrefs не ответит: тратить не от чего.
        </Text>
      ) : (
        <>
          <Text size="sm">
            Осталось <b>{formatNumber(key.left)}</b> из <b>{formatNumber(key.cap)}</b> — выбрано{' '}
            {key.used_percent ?? 0}%{key.binding === null ? '' : `, ${BINDING[key.binding]}`}
          </Text>
          <Meter spent={key.cap - key.left} cap={key.cap} label="Выбрано от капа Ahrefs за цикл" />
        </>
      )}
      <Group gap="sm" wrap="wrap">
        <Text size="sm" style={{ overflowWrap: 'anywhere' }}>
          {chosenLine(key)}
        </Text>
        {can('settings') ? (
          <Button
            size="compact-sm"
            variant="default"
            className="press"
            aria-haspopup="dialog"
            onClick={() => setEditing(true)}
          >
            Изменить кап
          </Button>
        ) : (
          <Text size="sm" c="dimmed">
            Кап меняет учётка с правом «{permissionTitle('settings')}»
          </Text>
        )}
      </Group>
      <Text size="sm" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
        {changedLine(key)}
      </Text>
      {/* Окно монтируется на каждое открытие — и открывается на действующем выборе. */}
      {editing && <AhrefsCapModal ahrefsKey={key} onClose={() => setEditing(false)} />}
    </Stack>
  );
}
