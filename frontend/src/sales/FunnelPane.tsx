/**
 * Воронка продаж: лиды на каждом шаге — от очереди до передачи телемаркетологу, по
 * гипотезам и периоду.
 *
 * **Экран ничего не считает сам.** Числа называет сервер (`GET /sales/funnel`) правилом
 * каждого шага — тем же, что у счётчиков и выгрузки; экран только делит доли и называет их
 * основу словами: «от отправленных», «от ответивших».
 *
 * **Лиды, а не письма.** Цепочка из трёх писем одному человеку — один отправленный лид.
 * Период — по первому письму лида: кто получил его в период, и всё, что с ним было потом.
 * Открытий и кликов нет: пиксель вредит доставляемости. MQL и SQL ставит телемаркетолог
 * в Kommo — воронка сервиса кончается передачей. На экране это одной строкой и в «i»,
 * определения шагов — «i» у подписи плитки (аудит экранов 09.10.2026).
 *
 * **Смена фильтра — не перезагрузка.** Прежние числа стоят приглушёнными, пока едут новые;
 * негодные свои даты на сервер не уходят — что не так, сказано под полем. Гипотеза и период
 * живут в адресе раздела (`leadFilters.ts`): переживают перезагрузку, гипотеза — и смену
 * вкладки.
 */

import {
  Alert,
  Group,
  Loader,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';

import { refusalOf } from '../api/client';
import type { FunnelCounts, FunnelRow, HypothesisCard, SalesFunnelView } from '../api/salesTypes';
import { InfoHint } from '../components/InfoHint';
import { Metric } from '../components/Metric';
import { formatNumber } from '../format';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';
import {
  funnelQuery,
  PERIOD_KEYS,
  PERIODS,
  periodProblem,
  stepHint,
  stepShare,
  STEPS,
  useSalesFunnel,
} from './funnelData';
import type { FunnelFilters, PeriodKey } from './funnelData';

const ALL = 'all';

const COLUMNS: Column[] = [
  { title: 'Гипотеза', width: '18rem' },
  ...STEPS.map((step) => ({ title: step.column, width: '7.5rem' })),
];

export const FUNNEL_MIN_WIDTH = 1008;

/** Что считает шаг — правилом сервера (`features/sales/funnel.py`), «i» у подписи плитки.
 *  До аудита экранов 09.10.2026 — абзацем под плитками. */
const STEP_INFO: Partial<Record<keyof FunnelCounts, string>> = {
  bounced: 'Вернулось хоть одно письмо цепочки: отказ сильнее доставки.',
  answered: 'Человек ответил сам или попросил больше не писать; автоответ ответом не считается.',
  handed_off: 'Передача телемаркетологу заведена. MQL и SQL ставит телемаркетолог в Kommo.',
};

function Filters({
  filters,
  hypotheses,
  problem,
  onChange,
}: {
  filters: FunnelFilters;
  hypotheses: HypothesisCard[];
  problem: string | null;
  onChange: (patch: Partial<FunnelFilters>) => void;
}) {
  // На узком окне — столбиком во всю ширину: в ряд «Свои даты»
  // уходили за край телефона (снимок 390 px, 07.10.2026).
  const narrow = useMediaQuery('(max-width: 36em)');
  return (
    <Stack gap="sm" px="md">
      <Group align="flex-end" gap="md" wrap="wrap">
        <Select
          label="Гипотеза"
          allowDeselect={false}
          data={[
            { value: ALL, label: 'Все гипотезы' },
            ...hypotheses.map((row) => ({ value: String(row.id), label: row.name })),
          ]}
          value={filters.hypothesis === null ? ALL : String(filters.hypothesis)}
          onChange={(value) => {
            if (value !== null) onChange({ hypothesis: value === ALL ? null : Number(value) });
          }}
          w={{ base: '100%', xs: 280 }}
        />
        <Stack gap={4} className="funnelPeriod" w={narrow ? '100%' : undefined}>
          <Text size="sm" fw={500} component="span">
            Период
          </Text>
          <SegmentedControl
            aria-label="Период"
            orientation={narrow ? 'vertical' : 'horizontal'}
            fullWidth={narrow}
            value={filters.period}
            onChange={(value) => {
              const next = PERIOD_KEYS.find((key) => key === value);
              if (next !== undefined) onChange({ period: next });
            }}
            data={PERIOD_KEYS.map((key: PeriodKey) => ({ value: key, label: PERIODS[key] }))}
          />
        </Stack>
      </Group>
      {filters.period === 'custom' ? (
        // Пояснение у обоих полей: у одного — и поля встали бы на разной высоте.
        <Group align="flex-start" gap="md" wrap="wrap">
          <TextInput
            type="date"
            label="Первый день"
            description="с начала дня"
            value={filters.from}
            onChange={(event) => onChange({ from: event.currentTarget.value })}
            w={180}
          />
          <TextInput
            type="date"
            label="Последний день"
            description="включительно"
            value={filters.to}
            error={problem}
            onChange={(event) => onChange({ to: event.currentTarget.value })}
            w={180}
          />
        </Group>
      ) : null}
    </Stack>
  );
}

function Tiles({ counts, stale }: { counts: FunnelCounts; stale: boolean }) {
  return (
    <SimpleGrid
      cols={{ base: 2, sm: 3, lg: 6 }}
      spacing="sm"
      px="md"
      className="staleRows funnelTiles"
      data-stale={stale || undefined}
      aria-busy={stale || undefined}
    >
      {STEPS.map((step) => (
        <Metric
          key={step.key}
          title={step.title}
          value={formatNumber(counts[step.key])}
          hint={stepHint(counts, step.key)}
          info={STEP_INFO[step.key]}
        />
      ))}
    </SimpleGrid>
  );
}

function Cell({ counts, step }: { counts: FunnelCounts; step: keyof FunnelCounts }) {
  const share = stepShare(counts, step);
  return (
    <Table.Td>
      <Text size="sm">{formatNumber(counts[step])}</Text>
      {share !== null ? (
        <Text size="xs" c="dimmed" className="funnelShare">
          {share}
        </Text>
      ) : null}
    </Table.Td>
  );
}

function ByHypothesis({ view, stale }: { view: SalesFunnelView; stale: boolean }) {
  const line = (key: string, name: string, counts: FunnelCounts, total = false) => (
    <Table.Tr key={key}>
      <Table.Td className="cellName wrapCell">
        <Text size="sm" fw={total ? 600 : 500}>
          {name}
        </Text>
      </Table.Td>
      {STEPS.map((step) => (
        <Cell key={step.key} counts={counts} step={step.key} />
      ))}
    </Table.Tr>
  );
  return (
    <Stack gap={6}>
      <Text size="sm" px="md" className="funnelTableNote">
        По гипотезам: доли доставки, отказа и ответа — от отправленных, передачи — от ответивших.
      </Text>
      <FixedTable
        columns={COLUMNS}
        minWidth={FUNNEL_MIN_WIDTH}
        className="funnelTable"
        horizontalSpacing="md"
        tabularNums
        stale={stale}
        label="Воронка по гипотезам"
        rows={[
          ...view.rows.map((row: FunnelRow) =>
            line(String(row.hypothesis_id), row.name, row.counts),
          ),
          line('total', 'Всего', view.total, true),
        ]}
      />
    </Stack>
  );
}

/** Вступление одной строкой, как считается — в «i» (аудит экранов 09.10.2026). */
function Intro() {
  return (
    <Group gap={4} wrap="nowrap" align="flex-start" px="md">
      <Text size="sm" className="funnelIntro">
        Лиды на каждом шаге: письмо в очереди, ушло, дошло или вернулось, человек ответил, лид
        передан телемаркетологу.
      </Text>
      <InfoHint name="Как считается воронка" width={340}>
        Считаем лидов, а не письма; период — по первому письму лида, и всё, что с ним было потом.
        Открытия и клики не считаем: пиксель вредит доставляемости.
      </InfoHint>
    </Group>
  );
}

function Empty({ filters }: { filters: FunnelFilters }) {
  return (
    <Text size="sm" px="md" className="funnelEmpty">
      {filters.period === 'all'
        ? 'Писем лидам ещё не было: очередь собирается на вкладке «Очередь писем».'
        : 'За этот период первые письма лидам не уходили и в очередь не вставали.'}
    </Text>
  );
}

interface PaneProps {
  hypotheses: HypothesisCard[];
  /** Гипотеза и период — из адреса раздела. */
  filters: FunnelFilters;
  onChange: (patch: Partial<FunnelFilters>) => void;
}

export function FunnelPane({ hypotheses, filters, onChange }: PaneProps) {
  const problem = periodProblem(filters);
  const funnel = useSalesFunnel(funnelQuery(filters, new Date()), problem === null);
  const view = funnel.data;
  const stale = funnel.isPlaceholderData || problem !== null;

  if (hypotheses.length === 0) {
    return (
      <Text size="sm" c="dimmed" px="md">
        Гипотез пока нет. Воронка считается по лидам гипотез — загрузите базу.
      </Text>
    );
  }
  return (
    <Stack gap="md">
      <Intro />
      <Filters filters={filters} hypotheses={hypotheses} problem={problem} onChange={onChange} />
      {view === undefined ? (
        funnel.error ? (
          <Alert color="red" title="Воронка не загрузилась" mx="md">
            {refusalOf(funnel.error)}
          </Alert>
        ) : (
          <Loader aria-label="Загружаем воронку" m="md" />
        )
      ) : (
        <>
          <Tiles counts={view.total} stale={stale} />
          {view.total.sent === 0 && view.total.queued === 0 ? <Empty filters={filters} /> : null}
          {view.hypothesis_id === null ? <ByHypothesis view={view} stale={stale} /> : null}
        </>
      )}
    </Stack>
  );
}
