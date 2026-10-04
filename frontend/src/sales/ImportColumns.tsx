/**
 * Шаг «Колонки»: какая колонка файла в какое поле лида ложится.
 *
 * **Угадано сервером, поправлено человеком.** Сервер узнаёт поля по
 * заголовкам (таблица синонимов), а человек видит каждую колонку с первыми
 * значениями из файла и меняет поле там, где угадано не то. Каждая правка —
 * новый предпросмотр: числа «станут лидами / отклонено» пересчитывает тот же
 * код, что будет писать в базу, а не экран.
 *
 * **Без колонки почты дальше не пройти** — и это сказано словами с тем, что
 * сделать, а не серой кнопкой без объяснения. Лиды без адреса сервер не
 * считает, поэтому «0 лидов» здесь было бы ложью.
 */

import { Alert, Button, Group, Select, Stack, Switch, Table, Text, Title } from '@mantine/core';

import { LEAD_FIELDS } from '../api/salesLabels';
import type { IntakeView, LeadField } from '../api/salesTypes';
import { formatNumber, plural } from '../format';
import { dropdownBelow } from '../theme';
import { fieldAt } from './importMapping';
import type { FieldMapping } from './importMapping';

const LEAD_FIELD_KEYS = Object.keys(LEAD_FIELDS) as LeadField[];

/** Значение «эту колонку не грузить» — своё слово, не пустая строка: у Mantine
 *  пустое значение неотличимо от «ничего не выбрано». */
const SKIP = 'skip';

const FIELD_CHOICES = [
  { value: SKIP, label: 'не грузить' },
  ...LEAD_FIELD_KEYS.map((field) => ({ value: field, label: LEAD_FIELDS[field] })),
];

/** Список по ширине содержимого: «домен компании» в поле шириной с колонку рвался бы. */
const WIDE_LIST = { ...dropdownBelow, width: 'max-content' } as const;

/** Сколько значений колонки показать: по трём видно, что в ней лежит. */
const SHOWN_VALUES = 3;

/** Первые непустые значения колонки — одной строкой через точку. */
function samplesOf(sample: string[][], column: number): string {
  const values = sample.map((row) => (row[column] ?? '').trim()).filter((value) => value !== '');
  return values.length === 0 ? '—' : values.slice(0, SHOWN_VALUES).join(' · ');
}

interface Props {
  found: IntakeView;
  mapping: FieldMapping;
  header: boolean;
  /** Идёт пересчёт: таблица приглушена, переключатели ждут. */
  busy: boolean;
  refusal: string | null;
  onField: (column: number, field: LeadField | null) => void;
  onHeader: (checked: boolean) => void;
  onBack: () => void;
  onNext: () => void;
}

export function ImportColumns({
  found,
  mapping,
  header,
  busy,
  refusal,
  onField,
  onHeader,
  onBack,
  onNext,
}: Props) {
  const rows = found.rows;
  const columns = found.columns.length;
  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="md">
        {/* Основа в 20rem: на телефоне переключатель уходит под текст, а не сжимает его. */}
        <Stack gap={4} style={{ flex: '1 1 20rem', minWidth: 0 }}>
          <Title order={5}>Колонки файла и поля лида</Title>
          <Text size="sm" c="dimmed" maw={720}>
            {found.source}: {formatNumber(rows)} {plural(rows, 'строка', 'строки', 'строк')} с
            данными, {formatNumber(columns)} {plural(columns, 'колонка', 'колонки', 'колонок')}.
            Поле угадано по заголовку колонки — поправьте, если угадано не то. Поле у колонки одно:
            назначенное другой колонке переезжает сюда.
          </Text>
        </Stack>
        <Switch
          label="Первая строка — заголовок"
          checked={header}
          disabled={busy}
          onChange={(event) => onHeader(event.currentTarget.checked)}
        />
      </Group>

      {found.needs_mapping && (
        <Alert color="yellow" title="Колонка почты не найдена">
          Укажите, в какой колонке адрес почты: без него строка лидом не станет, и до отчёта мастер
          не пускает.
        </Alert>
      )}
      {refusal !== null && (
        <Alert color="red" title="Сопоставление не принято">
          {refusal}
        </Alert>
      )}

      <Table.ScrollContainer minWidth={720} type="native" className="scrollSlim">
        <Table
          className="dataTable fixedTable columnsTable"
          layout="fixed"
          verticalSpacing="sm"
          horizontalSpacing="sm"
        >
          <colgroup>
            <col style={{ width: '14rem' }} />
            <col />
            <col style={{ width: '13rem' }} />
          </colgroup>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Колонка</Table.Th>
              <Table.Th>Первые значения</Table.Th>
              <Table.Th>Поле лида</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody
            className="staleRows"
            data-stale={busy || undefined}
            aria-busy={busy || undefined}
          >
            {found.columns.map((name, column) => (
              <Table.Tr key={column}>
                <Table.Td className="cellName">
                  <Text size="sm" fw={500}>
                    {name}
                  </Text>
                </Table.Td>
                <Table.Td className="wrapCell">
                  <Text size="xs" c="dimmed">
                    {samplesOf(found.sample, column)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Select
                    size="xs"
                    aria-label={`Поле для колонки ${name}`}
                    data={FIELD_CHOICES}
                    value={fieldAt(mapping, column) ?? SKIP}
                    allowDeselect={false}
                    comboboxProps={WIDE_LIST}
                    onChange={(value) =>
                      onField(column, LEAD_FIELD_KEYS.find((field) => field === value) ?? null)
                    }
                  />
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>

      <Group justify="space-between">
        <Button variant="subtle" className="press" onClick={onBack}>
          К источнику
        </Button>
        <Button className="press" disabled={found.needs_mapping || busy} onClick={onNext}>
          К отчёту
        </Button>
      </Group>
    </Stack>
  );
}
