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
 *
 * **Разделы — под «Подробнее», «как в настройках сервера» — один раз** (разведка 10.11.2026:
 * оговорка стояла трижды). Оговорка — у строки общего, как решено 29.10.2026: она о всём блоке,
 * и в строке раздела и в подвале её больше нет. Раздел у предела раскрывает подробности сразу;
 * метка «без потолка» — на виду, над раскрытием.
 */

import { Badge, Stack, Text } from '@mantine/core';
import { useState } from 'react';

import { permissionTitle } from '../api/labels';
import type { LlmCaps as Caps, LlmPartCard } from '../api/llmCaps';
import { useSession } from '../auth/AuthProvider';
import { Meter } from '../components/Meter';
import { formatNumber } from '../format';
import { changedBy } from './CapDialog';
import { LimitCard, LimitLine, nearCap } from './LimitCard';
import { LlmCapsModal, tokensWords } from './LlmCapsModal';

/** Общий — словами строки: «50 000 000 токенов в сутки» или «без потолка». */
function generalLine(caps: Caps): string {
  const what = caps.general === null ? 'без потолка' : tokensWords(caps.general);
  const from = caps.scheme === 'env' ? ' — как в настройках сервера' : '';
  return `Общий потолок на весь сервис: ${what}${from}`;
}

/** Осталось от общего — только когда учётка видит весь расход: без права «Продажи» «потрачено»
 *  идёт без продаж, и разность от общего потолка была бы неправдой. */
function leftOfGeneral(caps: Caps): number | null {
  if (caps.general === null || !caps.parts.some((part) => part.part === 'sales')) return null;
  return Math.max(0, caps.general - caps.spent);
}

/** Роздано и свободный остаток — только у сохранённого блока с числом. */
function freeLine(caps: Caps): string | null {
  if (caps.scheme === 'env' || caps.free_share === null || caps.free === null) return null;
  return `Роздано разделам ${100 - caps.free_share}% · свободный остаток ${caps.free_share}% — можно раздать ещё ${formatNumber(caps.free)} токенов`;
}

/** Лимит раздела словами: «лимит 20% · 10 000 000»; без лимита — чем он держится. Токены и сутки
 *  названы в карточке выше. */
function limitLine(part: LlmPartCard, caps: Caps): string {
  if (part.limit === null) {
    if (caps.scheme === 'parts')
      return `${part.title} — без потолка (лимит ${part.share ?? 0}% запомнен)`;
    return `${part.title} — своего потолка нет, держит общий`;
  }
  const share = part.share === null ? '' : ` ${part.share}% ·`;
  return `${part.title} — лимит${share} ${formatNumber(part.limit)}`;
}

function PartRow({ part, caps }: { part: LlmPartCard; caps: Caps }) {
  return (
    <Stack gap={4}>
      <LimitLine strong>{limitLine(part, caps)}</LimitLine>
      {part.limit === null || part.left === null ? (
        <Text size="sm" c="dimmed">
          Потрачено сегодня {formatNumber(part.spent)}
        </Text>
      ) : (
        <>
          <Text size="sm" c="dimmed">
            Осталось {formatNumber(part.left)} — выбрано {part.used_percent ?? 0}%
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
  const left = leftOfGeneral(caps);
  return (
    <>
      <LimitCard
        title="Модель"
        caption={left === null ? 'Потрачено сегодня' : 'Осталось сегодня'}
        value={formatNumber(left ?? caps.spent)}
        of={caps.general === null ? 'токенов' : `из ${formatNumber(caps.general)} токенов`}
        gauge={
          caps.general === null
            ? undefined
            : { spent: caps.spent, cap: caps.general, label: 'Выбрано от общего потолка за сутки' }
        }
        note={free ?? generalLine(caps)}
        when={`Сброс ${caps.reset_in}`}
        notice={
          caps.general === null ? (
            <Badge
              color="yellow"
              variant="light"
              className="limitBadge"
              style={{ alignSelf: 'flex-start' }}
            >
              без потолка — расход ничем не ограничен
            </Badge>
          ) : undefined
        }
        unfolded={caps.parts.some((part) => nearCap(part.spent, part.limit))}
        footer={
          last === undefined ? 'Потолки модели на экране не меняли' : `Изменены: ${changedBy(last)}`
        }
        action={{
          label: 'Изменить потолки',
          allowed: can('spend'),
          who: `Потолки меняет учётка с правом «${permissionTitle('spend')}»`,
          onClick: () => setEditing(true),
        }}
      >
        <Stack gap="xs">
          {caps.parts.map((part) => (
            <PartRow key={part.part} part={part} caps={caps} />
          ))}
        </Stack>
      </LimitCard>
      {/* Окно монтируется на каждое открытие — и открывается на сохранённом блоке. */}
      {editing && <LlmCapsModal caps={caps} onClose={() => setEditing(false)} />}
    </>
  );
}
