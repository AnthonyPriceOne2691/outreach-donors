/**
 * База знаний продаж: записи, из которых пишет агент.
 *
 * **Записи не удаляются — выключаются**: переключатель в строке говорит, видит ли
 * запись агент. Выключенная остаётся в списке — её включают обратно отсюда, — а
 * в «Что увидит агент» её нет. Версия базы над таблицей — та, что запишет
 * черновик агента: правка любой включённой записи её меняет.
 *
 * **Тексты живут только здесь**, в базе: репозиторий публичный. Первичное
 * наполнение — файлом через консоль (`outreach sales-kb-load`), правка — здесь.
 * Отказ сервера — словами: в окне правки над формой, у переключателя —
 * уведомлением с текстом сервера.
 */

import { Alert, Button, Group, Loader, Stack, Switch, Table, Text } from '@mantine/core';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { kbKindTitle } from '../api/salesLabels';
import type { KbEntryCard, KbView } from '../api/salesTypes';
import { InfoHint } from '../components/InfoHint';
import { formatDateTime, formatNumber } from '../format';
import { AgentPreview } from './AgentPreview';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';
import { useKb, useSavedEntry, useToggle } from './kbData';
import { KbEntryModal } from './KbEntryModal';

const COLUMNS: Column[] = [
  { title: 'Запись' },
  { title: 'Вид', width: '8.5rem' },
  { title: 'Язык', width: '5rem' },
  { title: 'Теги', width: '10rem' },
  { title: 'Правил', width: '13rem' },
  { title: 'Агент видит', width: '8rem' },
  { title: 'Действия', width: '8rem' },
];

export const KB_MIN_WIDTH = 1040;

function KbHead({
  view,
  onAdd,
  onPreview,
}: {
  view: KbView;
  onAdd: () => void;
  onPreview: () => void;
}) {
  return (
    // Поле `md` — текст на той же кромке, что текст ячеек таблицы и сводки
    // раздела: 32 px от края стекла (правило соседних карточек).
    <Group justify="space-between" align="flex-start" wrap="wrap" gap="md" px="md">
      {/* Основа в 20rem — как у сводки раздела: на телефоне кнопки уходят под текст. */}
      <Stack gap={4} style={{ flex: '1 1 20rem', minWidth: 0 }}>
        {/* Вступление — одной строкой, остальное — в «i» (аудит экранов 09.10.2026). */}
        <Group gap={4} wrap="nowrap" align="flex-start">
          <Text size="sm">Агент пишет только из включённых записей.</Text>
          <InfoHint name="Что агент видит из базы" width={340}>
            Выключенная остаётся в списке, но агент её не видит. Тексты живут только здесь, в базе.
          </InfoHint>
        </Group>
        <Text size="sm" c="dimmed" className="kbVersion">
          Версия базы <code>{view.version}</code> · агент видит {formatNumber(view.active)} из{' '}
          {formatNumber(view.total)}
        </Text>
      </Stack>
      <Group gap="xs">
        <Button variant="default" className="press" onClick={onPreview}>
          Что увидит агент
        </Button>
        <Button className="press" onClick={onAdd}>
          Добавить запись
        </Button>
      </Group>
    </Group>
  );
}

interface RowProps {
  row: KbEntryCard;
  /** Переключатель этой строки ждёт ответа сервера. */
  busy: boolean;
  onEdit: (row: KbEntryCard) => void;
  onToggle: (active: boolean) => void;
}

function KbRow({ row, busy, onEdit, onToggle }: RowProps) {
  return (
    <Table.Tr>
      <Table.Td className="wrapCell">
        <Text size="sm" fw={500} className="kbTitle">
          {row.title}
        </Text>
        <Text size="xs" c="dimmed" lineClamp={2} className="kbText">
          {row.text}
        </Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{kbKindTitle(row.kind)}</Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{row.language}</Text>
      </Table.Td>
      <Table.Td className="wrapCell">
        <Text size="xs" c="dimmed" className="kbTags">
          {row.tags.length > 0 ? row.tags.join(', ') : '—'}
        </Text>
      </Table.Td>
      <Table.Td className="wrapCell">
        <Text size="xs" className="cellName">
          {row.updated_by ?? '—'}
        </Text>
        <Text size="xs" c="dimmed">
          {formatDateTime(row.updated_at)}
        </Text>
      </Table.Td>
      <Table.Td>
        <Group justify="center">
          <Switch
            aria-label={`Агент видит «${row.title}»`}
            checked={row.active}
            disabled={busy}
            onChange={(event) => onToggle(event.currentTarget.checked)}
          />
        </Group>
      </Table.Td>
      <Table.Td>
        <Button size="compact-sm" variant="default" className="press" onClick={() => onEdit(row)}>
          Править
        </Button>
      </Table.Td>
    </Table.Tr>
  );
}

function KbEmpty() {
  return (
    <Stack gap={6} py="sm" px="md">
      <Text size="sm" fw={500}>
        Записей пока нет — агенту не из чего писать.
      </Text>
      <Text size="sm" c="dimmed">
        Заведите запись здесь или загрузите базу из файла:{' '}
        <code>outreach sales-kb-load --file база.json</code> — файл лежит вне репозитория.
      </Text>
    </Stack>
  );
}

function KbWaiting({ error }: { error: unknown }) {
  if (!error) return <Loader aria-label="Загружаем базу знаний" m="md" />;
  return (
    <Alert color="red" title="База знаний не загрузилась">
      {refusalOf(error)}
    </Alert>
  );
}

export function KbPane() {
  const kb = useKb();
  const toggle = useToggle();
  const announce = useSavedEntry();
  const [editing, setEditing] = useState<KbEntryCard | 'new' | null>(null);
  const [previewing, setPreviewing] = useState(false);
  if (kb.data === undefined) return <KbWaiting error={kb.error} />;

  const view = kb.data;
  const saved = (card: KbEntryCard) => {
    setEditing(null);
    void announce(card);
  };
  const rows = view.rows.map((row) => (
    <KbRow
      key={row.id}
      row={row}
      busy={toggle.isPending && toggle.variables?.id === row.id}
      onEdit={setEditing}
      onToggle={(active) => toggle.mutate({ id: row.id, active })}
    />
  ));

  return (
    <Stack gap="sm">
      <KbHead view={view} onAdd={() => setEditing('new')} onPreview={() => setPreviewing(true)} />
      {view.total === 0 ? (
        <KbEmpty />
      ) : (
        <FixedTable
          columns={COLUMNS}
          minWidth={KB_MIN_WIDTH}
          className="kbTable"
          label="Записи базы знаний"
          horizontalSpacing="md"
          rows={rows}
        />
      )}
      {editing !== null && (
        <KbEntryModal
          key={editing === 'new' ? 'new' : editing.id}
          entry={editing === 'new' ? null : editing}
          kinds={view.kinds}
          limits={view.limits}
          onClose={() => setEditing(null)}
          onSaved={saved}
        />
      )}
      {previewing && <AgentPreview onClose={() => setPreviewing(false)} />}
    </Stack>
  );
}
