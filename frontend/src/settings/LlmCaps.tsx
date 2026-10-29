/**
 * Потолки модели на экране «Расход» (слово Anthony 29.10.2026): общий потолок на весь сервис,
 * сколько роздано разделам и свободный остаток, у каждого раздела — лимит, потрачено сегодня и
 * осталось, когда сброс, кто и когда менял, — и «Изменить потолки» у учётки с правом «менять
 * потолки расхода» (у оператора его нет).
 *
 * **Устройство — как ключи в пространстве Ahrefs**: у раздела свой лимит процентом общего, и это
 * жёсткий потолок его расхода за сутки; что не роздано — свободный остаток, его никто не тратит,
 * пока администратор не добавит разделу. Пока блок не сохраняли — потолки из настроек сервера, и
 * экран так и говорит.
 *
 * **«Без потолка» — заметно**: расход модели тогда не держит ничто, и строка говорит это жёлтой
 * меткой; лимиты разделов запомнены.
 */

import { Badge, Button, Group, Stack, Text } from '@mantine/core';
import { useState } from 'react';

import { permissionTitle } from '../api/labels';
import type { LlmCaps as Caps, LlmPartCard } from '../api/llmCaps';
import { useSession } from '../auth/AuthProvider';
import { Meter } from '../components/Meter';
import { formatNumber } from '../format';
import { changedBy } from './AhrefsCapModal';
import { LlmCapsModal, tokensWords } from './LlmCapsModal';

/** Общий — словами строки: «50 000 000 токенов в сутки» или «без потолка». */
function generalLine(caps: Caps): string {
  const what = caps.general === null ? 'без потолка' : tokensWords(caps.general);
  const from = caps.scheme === 'env' ? ' — как в настройках сервера' : '';
  return `Модель — общий потолок на весь сервис: ${what}${from}`;
}

/** Роздано и свободный остаток — только у сохранённого блока с числом. */
function freeLine(caps: Caps): string | null {
  if (caps.scheme === 'env' || caps.free_share === null || caps.free === null) return null;
  return `Роздано разделам ${100 - caps.free_share}% · свободный остаток ${caps.free_share}% — можно раздать ещё ${formatNumber(caps.free)} токенов`;
}

/** Лимит раздела словами: «лимит 20% · 10 000 000 токенов»; без лимита — чем он держится. */
function limitLine(part: LlmPartCard, caps: Caps): string {
  if (part.limit === null) {
    if (caps.scheme === 'parts')
      return `${part.title} — без потолка (лимит ${part.share ?? 0}% запомнен)`;
    return `${part.title} — своего потолка нет, держит общий`;
  }
  const share = part.share === null ? '' : ` ${part.share}% ·`;
  const from = caps.scheme === 'env' ? ' — как в настройках сервера' : '';
  return `${part.title} — лимит${share} ${tokensWords(part.limit)}${from}`;
}

function PartRow({ part, caps }: { part: LlmPartCard; caps: Caps }) {
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500} style={{ overflowWrap: 'anywhere' }}>
        {limitLine(part, caps)}
      </Text>
      {part.limit === null || part.left === null ? (
        <Text size="sm" c="dimmed">
          Потрачено сегодня {formatNumber(part.spent)}
        </Text>
      ) : (
        <>
          <Text size="sm">
            Потрачено сегодня <b>{formatNumber(part.spent)}</b> · осталось{' '}
            <b>{formatNumber(part.left)}</b> — выбрано {part.used_percent ?? 0}%
          </Text>
          <Meter
            spent={part.spent}
            cap={part.limit}
            label={`Выбрано от лимита раздела «${part.title}»`}
          />
        </>
      )}
    </Stack>
  );
}

export function LlmCaps({ caps }: { caps: Caps }) {
  const { can } = useSession();
  const [editing, setEditing] = useState(false);
  const free = freeLine(caps);
  const [last] = caps.changes;
  return (
    <Stack gap="sm">
      <Group gap="sm" wrap="wrap">
        <Text size="sm" fw={500} style={{ overflowWrap: 'anywhere' }}>
          {generalLine(caps)}
        </Text>
        {caps.general === null && (
          <Badge color="yellow" variant="light">
            без потолка — расход модели ничем не ограничен
          </Badge>
        )}
      </Group>
      {free !== null && <Text size="sm">{free}</Text>}
      {caps.parts.map((part) => (
        <PartRow key={part.part} part={part} caps={caps} />
      ))}
      <Text size="sm" c="dimmed">
        Потрачено моделью сегодня: {formatNumber(caps.spent)} · сброс {caps.reset_in}
      </Text>
      <Group gap="sm" wrap="wrap">
        <Text size="sm" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
          {last === undefined
            ? 'Потолки модели на экране не меняли — как в настройках сервера'
            : `Изменены: ${changedBy(last)}`}
        </Text>
        {can('spend') ? (
          <Button
            size="compact-sm"
            variant="default"
            className="press"
            aria-haspopup="dialog"
            onClick={() => setEditing(true)}
          >
            Изменить потолки
          </Button>
        ) : (
          <Text size="sm" c="dimmed">
            Потолки меняет учётка с правом «{permissionTitle('spend')}»
          </Text>
        )}
      </Group>
      {/* Окно монтируется на каждое открытие — и открывается на сохранённом блоке. */}
      {editing && <LlmCapsModal caps={caps} onClose={() => setEditing(false)} />}
    </Stack>
  );
}
