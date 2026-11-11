/**
 * Переключатель вкладок «Диалоги · Не привязаны» — один на обе раскладки экрана.
 *
 * До 11.11.2026 его рисовали два места: колонка списка рядом с перепиской (`ThreadList`) и
 * страница списка (`ThreadsPage`) — каждое со своими подписями, размером и переходом. На
 * широком окне вкладку «Диалоги» рисовало первое, «Не привязаны» — второе, и переключатель
 * под указателем уезжал: у продаж на 55 px вниз, у рекламодателей на 21 px вниз и на 30 влево
 * (разведка экранов 10.11.2026).
 *
 * **Число — у каждой вкладки**, как у «Отбора»: «Не привязаны — 2». Без него вкладку, в которой
 * что-то лежит, не отличить от пустой. Число непривязанных спрашивается и с первой вкладки —
 * первой страницей, той же, что откроется на второй (`threadTabs.useUnboundPage`).
 *
 * **«Диалоги» возвращают туда, где на них были** — к открытому диалогу и фильтру
 * (`threadTabs.threadsPlace`), заменой записи в истории. Часть списка (`?stage=…`) вкладки
 * переживает. Недонабранный поиск сбрасывается: иначе он догнал бы новую вкладку своей
 * записью в адрес и увёл с неё.
 */

import { SegmentedControl } from '@mantine/core';
import { useNavigate } from 'react-router-dom';

import { usePageParam } from '../components/PageSwitch';
import { formatNumber } from '../format';
import {
  THREAD_TAB_KEYS,
  THREAD_TABS,
  threadsPlace,
  useUnboundPage,
  writeThreadTab,
} from './threadTabs';
import type { ThreadTab } from './threadTabs';
import type { ThreadListState } from './useThreadList';

/** «Не привязаны — 2»; пока числа нет — одно имя. */
function tabLabel(tab: ThreadTab, count: number | undefined): string {
  return count === undefined ? THREAD_TABS[tab] : `${THREAD_TABS[tab]} — ${formatNumber(count)}`;
}

interface Props {
  tab: ThreadTab;
  list: ThreadListState;
  /** Колонка списка на широком окне: мелко и во всю её ширину. Уход на «Не привязаны» оттуда —
   *  новой записью в истории: «назад» возвращает к открытой переписке. */
  column?: boolean;
  /** Телефон: во всю ширину, поровну. Иначе — по содержимому, у левого края. */
  phone?: boolean;
}

export function ThreadsTabs({ tab, list, column = false, phone = false }: Props) {
  const navigate = useNavigate();
  const [page] = usePageParam();
  const unbound = useUnboundPage(tab === 'unbound' ? page : 1);
  const counts: Record<ThreadTab, number | undefined> = {
    threads: list.total,
    unbound: unbound.data?.total,
  };
  const part = list.filters.part;
  const stretched = column || phone;

  const switchTab = (next: ThreadTab) => {
    list.setSearch('');
    if (next === 'threads') {
      void navigate(threadsPlace(part), { replace: true });
      return;
    }
    void navigate(
      { pathname: '/threads', search: writeThreadTab(next, part).toString() },
      { replace: !column },
    );
  };

  return (
    <SegmentedControl
      aria-label="Вкладки диалогов"
      fullWidth={stretched}
      size={column ? 'xs' : 'sm'}
      // По содержимому, а не во всю ширину: `Stack` растягивает и `inline-flex`.
      {...(stretched ? {} : { style: { alignSelf: 'flex-start' } })}
      value={tab}
      onChange={(value) => {
        const next = THREAD_TAB_KEYS.find((key) => key === value);
        if (next !== undefined && next !== tab) switchTab(next);
      }}
      data={THREAD_TAB_KEYS.map((key) => ({ value: key, label: tabLabel(key, counts[key]) }))}
    />
  );
}
