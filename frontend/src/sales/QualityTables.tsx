/**
 * Таблицы вкладки «Агент: качество»: ситуации с воротами автоответа, причины отклонения и
 * версии агента. Числа — сервера, доля — только от основы не меньше N (`qualityData.ts`).
 *
 * **Ворота автоответа — по ситуации, словами сервера**: значок итога и первая причина под ним.
 * Пороги — без правки и верное молчание, каждое по своей основе; флага автоответа здесь нет.
 *
 * Таблицы — фиксированной раскладкой с прокруткой на узком окне (`FixedTable`), как воронка:
 * решений в строках нет, карточками на телефоне их делать незачем.
 */

import { Badge, Stack, Table, Text } from '@mantine/core';

import type {
  AgentQualityView,
  CohortRow,
  GateView,
  QualityCounts,
  SituationRow,
} from '../api/salesQuality';
import { formatDate, formatNumber } from '../format';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';
import { decisionsOf, enough, heldOf, percentOf, silencesOf, versionName } from './qualityData';

/** Итог ворот словами значка и его цвет: мята — проходит, роза — нет, серый — мало данных. */
const GATES: Record<GateView['state'], { title: string; color: string }> = {
  passed: { title: 'проходит', color: 'green' },
  failed: { title: 'не проходит', color: 'red' },
  few: { title: 'данных мало', color: 'gray' },
};

const SITUATION_COLUMNS: Column[] = [
  { title: 'Ситуация', width: '11rem' },
  { title: 'Решений', width: '6rem' },
  { title: 'Как есть', width: '6.5rem' },
  { title: 'С правкой', width: '6.5rem' },
  { title: 'Отклонено', width: '6.5rem' },
  { title: 'Верное молчание', width: '8rem' },
  { title: 'Отдано', width: '6rem' },
  { title: 'Ворота автоответа', width: '17rem' },
];
export const SITUATIONS_MIN_WIDTH = 1088;

const REASON_COLUMNS: Column[] = [
  { title: 'Причина отклонения', width: '16rem' },
  { title: 'Отклонений', width: '8rem' },
  { title: 'Доля', width: '7rem' },
];
export const REASONS_MIN_WIDTH = 496;

const VERSION_COLUMNS: Column[] = [
  { title: 'Версия агента', width: '22rem' },
  { title: 'Черновики', width: '10rem' },
  { title: 'Решений', width: '6rem' },
  { title: 'Как есть', width: '6.5rem' },
  { title: 'Ложное молчание', width: '8rem' },
  { title: 'Отдано', width: '6rem' },
];
export const VERSIONS_MIN_WIDTH = 944;

/** Число и доля под ним — от основы, если её хватает; иначе одно число. Основы нет вовсе
 *  (агент в этой ситуации не молчит или не пишет) — прочерк: «0» читался бы промахом. */
function Share({
  part,
  whole,
  view,
  floor,
}: {
  part: number;
  whole: number;
  view: AgentQualityView;
  floor?: number;
}) {
  return (
    <Table.Td>
      <Text size="sm">{whole === 0 ? '—' : formatNumber(part)}</Text>
      {enough(whole, view.limits) ? (
        <Text size="xs" c="dimmed" className="qualityShare">
          {percentOf(part, whole, floor)}
        </Text>
      ) : null}
    </Table.Td>
  );
}

/** Число без доли — ячейкой таблицы. */
function Count({ value }: { value: number }) {
  return (
    <Table.Td>
      <Text size="sm">{formatNumber(value)}</Text>
    </Table.Td>
  );
}

/** Решений и «как есть» — первые числа строки у ситуации и у версии агента. */
function Decided({ counts, view }: { counts: QualityCounts; view: AgentQualityView }) {
  const decisions = decisionsOf(counts);
  return (
    <>
      <Count value={decisions} />
      <Share part={counts.as_is} whole={decisions} view={view} floor={view.limits.as_is} />
    </>
  );
}

function Gate({ gate }: { gate: GateView }) {
  const { title, color } = GATES[gate.state];
  return (
    <Table.Td className="wrapCell">
      <Stack gap={2} align="center">
        <Badge variant="light" color={color} className="qualityGate">
          {title}
        </Badge>
        {gate.said.length > 0 ? (
          <Text size="xs" c="dimmed" className="qualityGateWhy">
            {gate.said.join('; ')}
          </Text>
        ) : null}
      </Stack>
    </Table.Td>
  );
}

function SituationLine({ row, view }: { row: SituationRow; view: AgentQualityView }) {
  const counts: QualityCounts = row.counts;
  const decisions = decisionsOf(counts);
  return (
    <Table.Tr>
      <Table.Td className="cellName wrapCell">
        <Text size="sm" fw={500}>
          {row.title}
        </Text>
      </Table.Td>
      <Decided counts={counts} view={view} />
      <Share part={counts.edited} whole={decisions} view={view} />
      <Share part={counts.rejected} whole={decisions} view={view} />
      <Share
        part={counts.true_silence}
        whole={silencesOf(counts)}
        view={view}
        floor={view.limits.silence}
      />
      <Count value={heldOf(counts)} />
      <Gate gate={row.gate} />
    </Table.Tr>
  );
}

export function QualitySituations({ view, stale }: { view: AgentQualityView; stale: boolean }) {
  const { as_is: floor, silence, min_n: least } = view.limits;
  return (
    <Stack gap={6}>
      <Text size="sm" px="md" className="qualityTableNote">
        По ситуации письма. Ворота автоответа: без правки не ниже {floor}%, верное молчание не ниже{' '}
        {silence}% — каждое от своей основы, и решений в ней не меньше {least}. Автоответ ситуации
        включает человек.
      </Text>
      <FixedTable
        columns={SITUATION_COLUMNS}
        minWidth={SITUATIONS_MIN_WIDTH}
        className="qualitySituations"
        horizontalSpacing="md"
        tabularNums
        stale={stale}
        label="Качество агента по ситуации письма"
        rows={view.situations.map((row) => (
          <SituationLine key={row.situation ?? 'none'} row={row} view={view} />
        ))}
      />
    </Stack>
  );
}

export function QualityReasons({ view, stale }: { view: AgentQualityView; stale: boolean }) {
  const total = view.reasons.reduce((sum, row) => sum + row.count, 0);
  return (
    <Stack gap={6}>
      <Text size="sm" px="md" className="qualityTableNote">
        За что оператор отклонял готовые черновики — доля от всех отклонений.
      </Text>
      <FixedTable
        columns={REASON_COLUMNS}
        minWidth={REASONS_MIN_WIDTH}
        className="qualityReasons"
        horizontalSpacing="md"
        tabularNums
        stale={stale}
        label="Причины отклонения"
        rows={view.reasons.map((row) => (
          <Table.Tr key={row.reason}>
            <Table.Td className="cellName wrapCell">
              <Text size="sm">{row.reason}</Text>
            </Table.Td>
            <Table.Td>
              <Text size="sm">{formatNumber(row.count)}</Text>
            </Table.Td>
            <Table.Td>
              <Text size="sm" className="qualityShare">
                {enough(total, view.limits) ? percentOf(row.count, total) : '—'}
              </Text>
            </Table.Td>
          </Table.Tr>
        ))}
      />
    </Stack>
  );
}

function VersionLine({ row, view }: { row: CohortRow; view: AgentQualityView }) {
  const counts = row.counts;
  const marks = [
    row.current ? 'текущая' : null,
    row.cohort.id === view.shown.id ? 'показана' : null,
  ]
    .filter((mark) => mark !== null)
    .join(', ');
  return (
    <Table.Tr data-shown={row.cohort.id === view.shown.id || undefined}>
      <Table.Td className="cellName wrapCell">
        <Text size="sm" fw={500}>
          {versionName(row.cohort)}
        </Text>
        {marks !== '' ? (
          <Text size="xs" c="dimmed" className="qualityMarks">
            {marks}
          </Text>
        ) : null}
      </Table.Td>
      <Table.Td>
        <Text size="sm">
          {formatDate(row.first_at)} — {formatDate(row.last_at)}
        </Text>
      </Table.Td>
      <Decided counts={counts} view={view} />
      <Share part={counts.false_silence} whole={silencesOf(counts)} view={view} />
      <Count value={heldOf(counts)} />
    </Table.Tr>
  );
}

export function QualityVersions({ view, stale }: { view: AgentQualityView; stale: boolean }) {
  return (
    <Stack gap={6}>
      <Text size="sm" px="md" className="qualityTableNote">
        Версии агента — промпты и база знаний; числа каждой — только по её черновикам.
      </Text>
      <FixedTable
        columns={VERSION_COLUMNS}
        minWidth={VERSIONS_MIN_WIDTH}
        className="qualityVersions"
        horizontalSpacing="md"
        tabularNums
        stale={stale}
        label="Версии агента"
        rows={view.cohorts.map((row) => (
          <VersionLine key={row.cohort.id} row={row} view={view} />
        ))}
      />
    </Stack>
  );
}
