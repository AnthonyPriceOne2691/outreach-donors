/**
 * Гипотезы — кому и зачем пишем — и сколько у каждой лидов в каждом состоянии.
 *
 * Числа — ссылки на вкладку лидов с тем же фильтром: число на сводке — это
 * вход в список, где с ним работают, и искать рядом фильтр незачем. Ноль —
 * не ссылка: вести в пустую таблицу нечестно.
 *
 * Гипотеза заводится окном «Новая гипотеза» в строке вкладок (`HypothesisModal`) — тем же
 * правилом, что командой консоли, — и **правится тем же окном**: «Править» под именем в строке.
 * Там же — «Удалить гипотезу»: только пустую. Кнопка — в ячейке имени, слева: на телефоне
 * таблица уезжает вбок, а первая колонка видна без прокрутки.
 *
 * Описание — коммерческий текст: живёт данными в базе, а не в репозитории, и в письма не идёт;
 * его читает судья сегмента сбора базы (`SegmentPane`). **В строке — три строки и «показать
 * целиком»** (решение 15.10): описание до 4000 знаков растягивало строку на экран.
 *
 * **План работ** — своим запросом (`planApi.useLivePlans`, опрос, пока план идёт), сведённым по
 * гипотезе: над таблицей — строки планов с «Остановить план» / «Продолжить», в таблице — колонка
 * «План работ» (`PlanCell`), только когда в ней есть гипотезы плана: без плана таблица прежняя.
 * **Колонка плана — вторая, сразу за именем** (стенд 17.11): последней она уезжала за край карточки
 * — на 1440 с открытым меню видно 178 px из 288, на 1280 — 18, ни шапки, ни кнопок. Второй — имя и
 * план (560 px) видно без прокрутки от 1280. **С колонкой плана колонки уже** (стенд 18.11): по
 * самому длинному, что в них бывает, мерянному по живому тексту: «1 000 000» ссылкой ячейки
 * (69,4 px) и дата (77,4) — табличными цифрами (холст без них давал 68 и 69, и дата вылезала в поле
 * ячейки), шапка «Отклонены» (79,4) и поля ячейки (32); имени остаётся 17rem. Таблица — 1 088 px и встаёт в карточку на 1440 с
 * открытым меню (обёртка 1 090; было 1 200, и «Заведена» уезжала вбок). Ширина таблицы — сумма
 * колонок (`tableWidth`), а не число руками. Без плана таблица прежняя (912). От 768 px имя стоит
 * на месте (`stickyName`, как в отчёте приёмки), когда окно уже таблицы, — кнопки «Править» и
 * строки плана не теряют, чья строка. **На телефоне** (390) имя не закреплено (общее правило
 * `stickyName`): колонка плана — сразу за ним, первой после прокрутки, и в её ячейке своя строка
 * с именем гипотезы (`PlanCell`, только на узком экране) — иначе после прокрутки вбок не понять,
 * чья строка.
 */

import { Anchor, Button, Group, Stack, Table, Text } from '@mantine/core';
import { useState } from 'react';
import type { UIEvent } from 'react';
import { Link } from 'react-router-dom';

import type { HypothesisCard } from '../api/salesTypes';
import { formatDate, formatNumber } from '../format';
import { ClampText } from './ClampText';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';
import { HypothesisModal } from './HypothesisModal';
import { LEAD_STATE_KEYS, leadsLink } from './leadFilters';
import { placesOf, useLivePlans } from './planApi';
import type { PlanPlace } from './planApi';
import { PLAN_COLUMN_WIDTH, PlanCell } from './PlanCell';
import { PlanLines } from './PlanLine';

/** Пикселей в `rem`: ширины колонок — в `rem` (растут со шрифтом), ширина таблицы — в px. */
const ROOT_PX = 16;

/** Ширина таблицы — сумма её колонок (все — в `rem`): уже этого она не сжимается. */
export function tableWidth(columns: Column[]): number {
  return columns.reduce((sum, { width }) => sum + parseFloat(width ?? '0') * ROOT_PX, 0);
}

export const COLUMNS: Column[] = [
  { title: 'Гипотеза', width: '20rem' },
  { title: 'Новые', width: '7rem' },
  { title: 'Готовы', width: '7rem' },
  { title: 'Отклонены', width: '8rem' },
  { title: 'Всего', width: '7rem' },
  { title: 'Заведена', width: '8rem' },
];

/** Число лидов до «1 000 000» — 69,4 px ссылкой ячейки, с полями 32 px (стенд 18.11). */
const COUNT_WIDTH = '6.375rem';

/** Колонка «План работ» — только когда в таблице есть гипотезы плана, второй — за именем. Колонки
 *  чисел — по самому длинному в них, а не с запасом: так таблица встаёт рядом с открытым меню. */
export const WITH_PLAN: Column[] = [
  { title: 'Гипотеза', width: '17rem' },
  { title: 'План работ', width: PLAN_COLUMN_WIDTH, start: true },
  { title: 'Новые', width: COUNT_WIDTH },
  { title: 'Готовы', width: COUNT_WIDTH },
  // Шапка «Отклонены» — 79,4 px и поля.
  { title: 'Отклонены', width: '7rem' },
  { title: 'Всего', width: COUNT_WIDTH },
  // Дата «16.11.2026» табличными цифрами — 77,4 px и поля.
  { title: 'Заведена', width: '6.875rem' },
];
/** Строк описания в свёрнутом виде. */
export const DESCRIPTION_LINES = 3;

function Count({ value, to }: { value: number; to: string }) {
  if (value === 0) {
    return (
      <Text size="sm" c="dimmed">
        0
      </Text>
    );
  }
  // Чернилами с подчёркиванием (`inkLink`), а не бирюзой: бирюзовое число на светлом стекле
  // намерилось 4,25 : 1 при норме 4,5 (замер 09.10.2026) — тот же урок, что у «Переписки»
  // на экране писем 07.10.
  return (
    <Anchor
      component={Link}
      to={to}
      size="sm"
      fw={500}
      c="var(--ink)"
      underline="always"
      className="inkLink"
    >
      {formatNumber(value)}
    </Anchor>
  );
}

function HypothesisRow({
  row,
  onEdit,
  plan,
}: {
  row: HypothesisCard;
  onEdit: (row: HypothesisCard) => void;
  /** Колонка «План работ» есть: у гипотезы плана — её место, у остальных — `undefined`. */
  plan: { place: PlanPlace | undefined } | null;
}) {
  return (
    <Table.Tr>
      <Table.Td className="cellName wrapCell">
        <Group gap="xs" align="center" wrap="wrap">
          <Text size="sm" fw={500}>
            {row.name}
          </Text>
          <Button
            size="compact-sm"
            variant="default"
            className="press"
            aria-label={`Править гипотезу «${row.name}»`}
            onClick={() => onEdit(row)}
          >
            Править
          </Button>
        </Group>
        {row.description !== null && (
          <ClampText
            text={row.description}
            lines={DESCRIPTION_LINES}
            className="hypothesisDescription"
            size="xs"
            dimmed
            toggle="link"
          />
        )}
      </Table.Td>
      {plan !== null && <PlanCell place={plan.place} card={row} />}
      {LEAD_STATE_KEYS.map((state) => (
        <Table.Td key={state}>
          <Count value={row.leads[state]} to={leadsLink(row.id, state)} />
        </Table.Td>
      ))}
      <Table.Td>
        <Count value={row.total} to={leadsLink(row.id, null)} />
      </Table.Td>
      <Table.Td>
        <Text size="sm">{formatDate(row.created_at)}</Text>
      </Table.Td>
    </Table.Tr>
  );
}

/** Таблица уехала вбок — имя закреплённой колонки залито (`stickyScrolled`): прокрутка вбок —
 *  событие самой области таблицы, оно не всплывает и ловится на погружении. */
function useScrolled() {
  const [scrolled, setScrolled] = useState(false);
  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    setScrolled((event.target as HTMLElement).scrollLeft > 0);
  };
  return { scrolled, onScroll };
}

interface Props {
  rows: HypothesisCard[];
  /** Гипотеза удалена: снять её из адреса раздела, если она выбрана. */
  onRemoved?: (id: number) => void;
}

export function HypothesesTable({ rows, onRemoved }: Props) {
  const [editing, setEditing] = useState<HypothesisCard | null>(null);
  const plans = useLivePlans();
  const { scrolled, onScroll } = useScrolled();
  const places = placesOf(plans.data);
  const planned = rows.some((row) => places.has(row.id));
  if (rows.length === 0) {
    return (
      <Stack gap={6} py="sm">
        <Text size="sm" fw={500}>
          Гипотез пока нет.
        </Text>
        {/* Зовёт к кнопке словами, а не второй кнопкой: «Новая гипотеза» уже стоит в строке
            вкладок — одно действие, одна кнопка. */}
        <Text size="sm" c="dimmed">
          Гипотеза — кому и зачем пишем. Заведите её кнопкой «Новая гипотеза» выше — базу под неё
          грузит мастер загрузки.
        </Text>
      </Stack>
    );
  }
  return (
    <>
      <PlanLines plans={plans.data?.plans} error={plans.error} />
      <div className={scrolled ? 'stickyScrolled' : undefined} onScrollCapture={onScroll}>
        <FixedTable
          columns={planned ? WITH_PLAN : COLUMNS}
          minWidth={tableWidth(planned ? WITH_PLAN : COLUMNS)}
          className={planned ? 'hypothesesTable stickyName' : 'hypothesesTable'}
          horizontalSpacing="md"
          tabularNums
          rows={rows.map((row) => (
            <HypothesisRow
              key={row.id}
              row={row}
              onEdit={setEditing}
              plan={planned ? { place: places.get(row.id) } : null}
            />
          ))}
        />
      </div>
      {editing !== null && (
        <HypothesisModal
          key={editing.id}
          card={editing}
          onClose={() => setEditing(null)}
          onRemoved={(card) => onRemoved?.(card.id)}
        />
      )}
    </>
  );
}
