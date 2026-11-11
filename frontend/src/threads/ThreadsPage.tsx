/**
 * Диалоги: список переписок с донорами и вкладка «Не привязаны» — на узком окне.
 * На широком окне список стоит колонкой рядом с перепиской, а «Не привязаны» — той же
 * карточкой на всю ширину (`ThreadsScreen`, `ThreadList`).
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
 * Вкладки — шапкой панели со строками, со счётчиками (`ThreadsTabs`); вкладка и её
 * страница — в адресе (`threadTabs.ts`).
 */

import { Alert, Card, Loader, Stack } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useSearchParams } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { usePageParam } from '../components/PageSwitch';
import { LeadsExport } from './LeadsExport';
import { ThreadRows } from './ThreadList';
import { hasLeads, isThreadFiltered } from './threadFilters';
import { ThreadsTabs } from './ThreadsTabs';
import { ThreadsTitle } from './ThreadsTitle';
import { readThreadTab, useUnboundPage } from './threadTabs';
import type { ThreadTab } from './threadTabs';
import { UnboundReplies } from './UnboundReplies';
import { useThreadList } from './useThreadList';
import type { ThreadListState } from './useThreadList';

interface HeadProps {
  list: ThreadListState;
  narrow: boolean;
  tab: ThreadTab;
}

/** Шапка: заголовок части, число и выгрузка лидов. Лиды файлом — ответы на оффер
 *  рекламодателю: у частей доноров и продаж их нет, и кнопки там нет. На телефоне
 *  заголовок на ступень меньше (`ThreadsTitle`, `compact`). «i» поясняет открытую вкладку. */
function Head({ list, narrow, tab }: HeadProps) {
  const part = list.filters.part;
  return (
    <Card className="glassPanel" px="xl" py="lg">
      <ThreadsTitle
        total={list.total}
        found={isThreadFiltered(list.typed) ? list.shown.length : null}
        part={part}
        compact={narrow}
        aside={hasLeads(part) ? <LeadsExport /> : undefined}
        tab={tab}
      />
    </Card>
  );
}

export function ThreadsPage() {
  const [params] = useSearchParams();
  const tab = readThreadTab(params);
  const [page, goToPage] = usePageParam();
  // Число непривязанных спрашивается сразу, не дожидаясь списка диалогов: без него вкладку, в
  // которой что-то лежит, не отличить от пустой, а её первая страница из кэша открывает вкладку
  // сразу. Тот же ключ кэша, что у переключателя (`ThreadsTabs`) и самой вкладки.
  useUnboundPage(tab === 'unbound' ? page : 1);
  // На узком окне вкладки — во всю ширину, поровну, а заголовок — на ступень меньше.
  // Значение — с первой отрисовки: иначе телефон на миг рисовал бы крупный заголовок.
  const narrow =
    useMediaQuery('(max-width: 36em)', false, { getInitialValueInEffect: false }) === true;

  const list = useThreadList();
  const { isLoading, error } = list.query;

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

  return (
    <Stack gap="lg">
      <Head list={list} narrow={narrow} tab={tab} />

      <Card className="glass tableCard" p="md">
        <Stack gap="sm">
          {/* Смена вкладки — замена записи в истории, как у «Отбора» (`ThreadsTabs`). */}
          <ThreadsTabs tab={tab} list={list} phone={narrow} />
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
