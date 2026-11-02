/**
 * Что смотреть на «Рекламодателях»: панель, проверку, обход доноров, всех рекламодателей списком
 * или приёмку по донорам — переключателем вверху экрана, видом в адресе (`?view=list`).
 *
 * **Обход доноров — своей вкладкой** (Anthony 29.10.2026): доноров будут тысячи, и панель над
 * спорными вываливала их простынёй. На вкладке — страницы, фильтры и счётчики (`CrawlTab`), в
 * «Проверке» — сводка одной строкой (`CrawlSummary`).
 *
 * Сверка ТЗ 11.10.2026 добавила экрану список с выгрузкой и отчёт приёмки. Стопкой под
 * очередью они увели бы вниз то, ради чего экран открывают каждый день, — спорных
 * и купленных; отдельным разделом меню — разорвали бы рекламодателей на два места.
 *
 * **Панель — первый вид и вид по умолчанию** (тест-группа 31.10.2026, `AdvertiserPanel`): путь от
 * донора до письма одним взглядом. Адрес экрана из меню — панель, проверка — `?view=review`.
 *
 * **Смена вида — чистый адрес**: фильтры и страница одного вида другому ничего
 * не говорят, а страница бизнесов ниши (`niche_page`) — только проверке.
 *
 * **На телефоне — столбиком во всю ширину**, как переключатель этапа
 * (`components/StageSwitch`): пять видов в ряд за край экрана не уместятся.
 */

import { Card, SegmentedControl } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

export type AdvertiserView = 'panel' | 'review' | 'crawl' | 'list' | 'acceptance';

const VIEWS: { value: AdvertiserView; label: string }[] = [
  { value: 'panel', label: 'Панель' },
  { value: 'review', label: 'Проверка' },
  { value: 'crawl', label: 'Обход доноров' },
  { value: 'list', label: 'Все рекламодатели' },
  { value: 'acceptance', label: 'Приёмка по донорам' },
];

/** Вид — из адреса; незнакомый или пустой — панель. */
export function useAdvertiserView(): [AdvertiserView, (view: AdvertiserView) => void] {
  const [params, setParams] = useSearchParams();
  const asked = params.get('view');
  const view = VIEWS.find((known) => known.value === asked)?.value ?? 'panel';
  const go = useCallback(
    (next: AdvertiserView) => {
      if (next === view) return;
      setParams(next === 'panel' ? new URLSearchParams() : new URLSearchParams({ view: next }));
    },
    [view, setParams],
  );
  return [view, go];
}

/** Узкое окно — то же, что у переключателя этапа. */
const NARROW = '(max-width: 36em)';

export function ViewSwitch({
  view,
  onView,
}: {
  view: AdvertiserView;
  onView: (view: AdvertiserView) => void;
}) {
  const narrow = useMediaQuery(NARROW, false, { getInitialValueInEffect: false }) === true;
  return (
    <Card className="glassPanel" p="md">
      <SegmentedControl
        aria-label="Что смотреть на экране «Рекламодатели»"
        value={view}
        onChange={(picked) => onView(VIEWS.find((known) => known.value === picked)?.value ?? view)}
        data={VIEWS}
        orientation={narrow ? 'vertical' : 'horizontal'}
        fullWidth={narrow}
        style={{ alignSelf: narrow ? 'stretch' : 'flex-start' }}
      />
    </Card>
  );
}
