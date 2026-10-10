/**
 * Диалоги: список переписок с донорами — на узком окне и вкладка «Не привязаны».
 * На широком окне список стоит колонкой рядом с перепиской (`ThreadsScreen`).
 *
 * **Строка — это донор, а не адрес.** У сайта несколько адресов —
 * `info@`, `editor@`, `advertising@`, — и за ними одна редакция. Иначе
 * один донор занимает пять строк, и непонятно, с кем из них уже
 * договорились.
 *
 * **Состояние считает сервер по последнему событию**, а не хранит полем:
 * отдельное поле рассинхронизируется с письмами при первом же сбое,
 * и список врёт именно там, где по нему принимают решения.
 *
 * **Строки — в два яруса, а не таблицей** (`ThreadList`, с 09.10.2026): таблица в пять
 * колонок уже 1 200 px уезжала в прокрутку вбок, а домен рвался посреди имени.
 * Фильтры — над строками и в адресе (`threadFilters.ts`); счётчики состояний живут
 * в самом фильтре — «ждёт разбора · 1» (аудит 25.09.2026).
 *
 * **Шапка — одной строкой** (`ThreadsTitle`): пояснение и калибровка разбора — в «i».
 *
 * **Вторая вкладка — «Не привязаны»** (28.09.2026): ответы, которые приём
 * сохранил, но не соотнёс ни с одним нашим письмом (`UnboundReplies`).
 * Вкладки — шапкой панели со строками, со счётчиками; вкладка и её страница —
 * в адресе (`threadTabs.ts`). Число непривязанных спрашивается и на первой
 * вкладке: без него вкладку, в которой что-то лежит, не отличить от пустой,
 * а первая её страница из кэша открывает вкладку сразу.
 */

import { Alert, Card, Group, Loader, SegmentedControl, Stack } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { usePageParam } from '../components/PageSwitch';
import { formatNumber } from '../format';
import { LeadsExport } from './LeadsExport';
import { ThreadRows } from './ThreadList';
import { isThreadFiltered } from './threadFilters';
import { ThreadsTitle } from './ThreadsTitle';
import {
  readThreadTab,
  THREAD_TAB_KEYS,
  THREAD_TABS,
  threadsPlace,
  useUnboundPage,
  writeThreadTab,
} from './threadTabs';
import type { ThreadTab } from './threadTabs';
import { UnboundReplies } from './UnboundReplies';
import { useThreadList } from './useThreadList';

/** «Не привязаны — 2», как у вкладок «Отбора»; пока числа нет — одно имя. */
function tabLabel(tab: ThreadTab, count: number | undefined): string {
  return count === undefined ? THREAD_TABS[tab] : `${THREAD_TABS[tab]} — ${formatNumber(count)}`;
}

export function ThreadsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab = readThreadTab(params);
  const [page, goToPage] = usePageParam();
  // На первой вкладке — ради числа во вкладке: первая страница, та же, что
  // откроется на второй.
  const unbound = useUnboundPage(tab === 'unbound' ? page : 1);
  // На узком окне вкладки — во всю ширину, поровну.
  const narrow = useMediaQuery('(max-width: 36em)') === true;

  const list = useThreadList();
  const { data, isLoading, error } = list.query;

  // Крутилка и отказ — только у своей вкладки: вторая от списка диалогов
  // не зависит и ждать его не должна.
  if (tab === 'threads' && isLoading) return <Loader aria-label="Загружаем диалоги" m="md" />;
  if (tab === 'threads' && error) {
    return (
      <Alert color="red" title="Диалоги не загрузились" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const tabCounts: Record<ThreadTab, number | undefined> = {
    threads: data?.length,
    unbound: unbound.data?.total,
  };
  // Смена вкладки — замена записи в истории, как у «Отбора»; недонабранный
  // поиск сбрасывается, чтобы не догнать новую вкладку своей записью в адрес.
  // «Диалоги» — туда, где на этой вкладке были: к открытому диалогу и фильтру.
  const switchTab = (next: ThreadTab) => {
    list.setSearch('');
    if (next === 'threads') void navigate(threadsPlace(), { replace: true });
    else setParams(writeThreadTab(next), { replace: true });
  };

  return (
    <Stack gap="lg">
      <Card className="glassPanel" px="xl" py="lg">
        <Group justify="space-between" gap="sm">
          <ThreadsTitle
            total={data?.length}
            found={isThreadFiltered(list.typed) ? list.shown.length : null}
          />
          <LeadsExport />
        </Group>
      </Card>

      <Card className="glass tableCard" p="md">
        <Stack gap="sm">
          {/* По содержимому, а не во всю ширину: `Stack` растягивает и `inline-flex`. */}
          <SegmentedControl
            aria-label="Вкладки диалогов"
            fullWidth={narrow}
            style={narrow ? undefined : { alignSelf: 'flex-start' }}
            value={tab}
            onChange={(value) => {
              const next = THREAD_TAB_KEYS.find((key) => key === value);
              if (next !== undefined && next !== tab) switchTab(next);
            }}
            data={THREAD_TAB_KEYS.map((key) => ({
              value: key,
              label: tabLabel(key, tabCounts[key]),
            }))}
          />
          {tab === 'unbound' ? (
            <UnboundReplies page={page} onPage={goToPage} />
          ) : (
            <ThreadRows list={list} split={false} />
          )}
        </Stack>
      </Card>
    </Stack>
  );
}
