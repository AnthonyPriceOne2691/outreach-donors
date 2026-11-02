/**
 * Окно панели: что стало с донорами выборки — от постановки на обход до дошедшего письма.
 *
 * Тест-группа 31.10.2026: от «добавили сто доноров» до «скольким дошло письмо» путь не был виден
 * одним взглядом. Выборка — сегодня (по умолчанию), неделя, всё время или одна партия; она живёт
 * в адресе (`?range=week`, `?range=load&load=12`): ссылка коллеге открывает те же числа.
 *
 * **Экран своего счёта не ведёт**: шаги считает сервер (`crawl/panel.py`) правилами приёмки, и
 * все они — из одной выборки. Как считается — в «i» у заголовка: это читают раз, а не каждый день.
 *
 * **Смена выборки — не перезагрузка**: прежние числа стоят приглушённо, пока едут новые.
 */

import {
  Alert,
  Card,
  Loader,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
} from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

import { fetchPanel } from '../api/advertiserPanel';
import type {
  PanelAdvertisers,
  PanelDonors,
  PanelLoad,
  PanelRange,
  PanelView,
} from '../api/advertiserPanel';
import { refusalOf } from '../api/client';
import { Metric } from '../components/Metric';
import { PageHead } from '../components/PageHead';
import { formatDateTime, formatNumber } from '../format';

export const PANEL_KEY = ['advertisers', 'panel'] as const;

const RANGES: { value: PanelRange; label: string }[] = [
  { value: 'today', label: 'Сегодня' },
  { value: 'week', label: 'Неделя' },
  { value: 'all', label: 'Всё время' },
  { value: 'load', label: 'Партия' },
];

const ABOUT_LINES = [
  'Выборка — обходы, заказанные за период: сутки — по Москве, неделя — семь суток, считая сегодня. Партия — домены одной загрузки и их обходы с минуты загрузки; загрузки раньше этого экрана партиями не записаны.',
  'Доноров поставлено — заказан обход или донор ждёт в очереди загрузки. Обход начался — открыта хоть одна страница.',
  'Рекламодателей получено — домены, найденные обходами выборки: «куплено» и все, кому человек сказал «пишем». Домен двух доноров — один, поставщик не считается.',
  'Новый — домен не встречался ни в одном обходе раньше выборки и не был рекламодателем. Повторный — встречался. У «всего времени» раньше ничего нет, и повторных нет.',
  'С адресом — из переведённых в рекламодатели: искать адрес начинают после перевода.',
  'Письмо ушло — платформа приняла хоть одно письмо Этапа 2 этому домену, отказ доставки тоже. Письмо дошло — платформа сообщила о доставке. Домену письмо одно, поэтому у повторного оно могло уйти раньше выборки.',
];

const ABOUT = (
  <Stack gap={6}>
    {ABOUT_LINES.map((line) => (
      <span key={line}>{line}</span>
    ))}
  </Stack>
);

/** Выборка из адреса; незнакомая или пустая — сегодня, партия без годного номера — без партии. */
export function usePanelRange(): {
  range: PanelRange;
  load: number | null;
  choose: (range: PanelRange, load?: number | null) => void;
} {
  const [params, setParams] = useSearchParams();
  const asked = params.get('range');
  const range = RANGES.find((known) => known.value === asked)?.value ?? 'today';
  const number = Number(params.get('load'));
  const load = range === 'load' && Number.isInteger(number) && number >= 1 ? number : null;
  const choose = useCallback(
    (next: PanelRange, nextLoad: number | null = null) => {
      setParams(
        (was) => {
          const moved = new URLSearchParams(was);
          moved.delete('load');
          // Сегодня — без слова: адрес экрана тот же, что в меню.
          if (next === 'today') moved.delete('range');
          else moved.set('range', next);
          if (next === 'load' && nextLoad !== null) moved.set('load', String(nextLoad));
          return moved;
        },
        { replace: true },
      );
    },
    [setParams],
  );
  return { range, load, choose };
}

/** Партия в списке выбора: когда, чем и сколько доменов. */
function loadLabel(load: PanelLoad): string {
  const name = load.kind === 'manual' ? 'вручную' : (load.file_name ?? 'файлом');
  return `${formatDateTime(load.at)} · ${name} · ${formatNumber(load.hosts)}`;
}

/** Где доноры сейчас — словами под числом: нули не называются. */
function donorsHint(donors: PanelDonors): string | undefined {
  const said = [
    donors.running > 0 ? `идёт ${formatNumber(donors.running)}` : '',
    donors.refused > 0 ? `не открылись ${formatNumber(donors.refused)}` : '',
  ].filter(Boolean);
  return said.length > 0 ? said.join(' · ') : undefined;
}

/** Кто ещё не в обходе — словами под числом поставленных. */
function waitingHint(donors: PanelDonors): string | undefined {
  const said = [
    donors.waiting > 0 ? `ждут очереди ${formatNumber(donors.waiting)}` : '',
    donors.idle > 0 ? `без обхода ${formatNumber(donors.idle)}` : '',
  ].filter(Boolean);
  return said.length > 0 ? said.join(' · ') : undefined;
}

function Steps({ donors, advertisers }: { donors: PanelDonors; advertisers: PanelAdvertisers }) {
  return (
    <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }} spacing="sm">
      <Metric
        title="Доноров поставлено"
        value={formatNumber(donors.added)}
        hint={waitingHint(donors)}
      />
      <Metric
        title="Обход начался"
        value={formatNumber(donors.started)}
        hint={donorsHint(donors)}
      />
      <Metric
        title="Рекламодателей получено"
        value={formatNumber(advertisers.found)}
        hint={`новых ${formatNumber(advertisers.fresh)} · повторных ${formatNumber(advertisers.repeat)}`}
      />
      <Metric
        title="С адресом"
        value={formatNumber(advertisers.with_address)}
        hint={`переведено ${formatNumber(advertisers.promoted)}`}
      />
      <Metric title="Письмо ушло" value={formatNumber(advertisers.sent)} />
      <Metric title="Письмо дошло" value={formatNumber(advertisers.delivered)} />
    </SimpleGrid>
  );
}

function LoadPick({
  loads,
  load,
  onLoad,
}: {
  loads: PanelLoad[];
  load: number | null;
  onLoad: (load: number) => void;
}) {
  const data = useMemo(
    () => loads.map((row) => ({ value: String(row.id), label: loadLabel(row) })),
    [loads],
  );
  if (loads.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Партий пока нет: они записываются с первой загрузки файлом или вручную. Обходы прежних
        загрузок — в таблице ниже.
      </Text>
    );
  }
  return (
    <Select
      aria-label="Какая партия"
      placeholder="Выберите партию"
      data={data}
      value={load === null ? null : String(load)}
      onChange={(picked) => {
        if (picked !== null) onLoad(Number(picked));
      }}
      allowDeselect={false}
      maw="26rem"
    />
  );
}

/** Что стоит под выборкой: отказ словами, крутилка до первого ответа или шаги. Партия не выбрана —
 *  ничего: числа ответа — про всё время, а не про партию. */
function Numbers({ query, asked }: { query: UseQueryResult<PanelView>; asked: boolean }) {
  const { data, error } = query;
  if (error) {
    return (
      <Alert color="red" title="Панель не загрузилась">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (data === undefined) return <Loader aria-label="Считаем панель" m="md" />;
  if (asked) return null;
  return (
    <div
      className="staleRows"
      data-stale={query.isPlaceholderData || undefined}
      aria-busy={query.isPlaceholderData || undefined}
    >
      <Steps donors={data.donors} advertisers={data.advertisers} />
    </div>
  );
}

export function PanelSteps() {
  const { range, load, choose } = usePanelRange();
  const asked = range === 'load' && load === null;
  const query = useQuery({
    queryKey: [...PANEL_KEY, range, load],
    // Партия ещё не выбрана — выбирать есть из чего: список партий приходит с «всем временем».
    queryFn: () => fetchPanel(asked ? 'all' : range, load),
    placeholderData: keepPreviousData,
  });
  return (
    <Card className="glassPanel" p="lg">
      <Stack gap="sm">
        <PageHead title="Панель рекламодателей" hint={ABOUT} />
        <SegmentedControl
          aria-label="Какую выборку смотреть"
          value={range}
          onChange={(picked) => choose(picked as PanelRange)}
          data={RANGES}
          style={{ alignSelf: 'flex-start' }}
        />
        {range === 'load' && query.data !== undefined ? (
          <LoadPick loads={query.data.loads} load={load} onLoad={(next) => choose('load', next)} />
        ) : null}
        <Numbers query={query} asked={asked} />
      </Stack>
    </Card>
  );
}
