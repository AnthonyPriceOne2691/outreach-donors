/**
 * Раздел «Диалоги»: на широком окне — список слева и открытая переписка справа,
 * на узком — список и переписка отдельными страницами, как до 09.10.2026.
 *
 * Замечание Anthony 09.10.2026: «много места пустого — сдвинуть шапку и таблицу влево,
 * сократить, а справа сразу отображать диалог, не в карточке». Так устроены почта и
 * переписка у Outlook, Gmail, Intercom, Front: список — колонкой постоянной ширины,
 * переписка — остатком (`layout/split`).
 *
 * **Адрес — один на обе раскладки**: `/threads/42?state=needs_review`. Его открывают,
 * копируют и присылают; узкое окно рисует по нему одну переписку, широкое — список с
 * выделенной строкой и переписку рядом. Фильтры живут в строке параметров и переживают
 * переход к соседнему диалогу.
 *
 * **Список не пересоздаётся** при выборе диалога: раздел — один маршрут с вложенными
 * (`App.tsx`), а рама держит ключ по разделу (`workKey`). Иначе щелчок по строке
 * сбрасывал бы прокрутку списка и заново проигрывал подъём экрана.
 *
 * **Вкладка «Не привязаны» — на всю ширину**, без правой колонки: у ответа без письма
 * своей переписки нет, открывать справа нечего. Вкладка «Диалоги» возвращает оттуда
 * к открытому диалогу с его фильтром (`threadTabs.threadsPlace`) — своей части. Адрес,
 * который переписка поправила заменой (`threadPart.ts`: ссылка без части или с чужой
 * частью), чужой части не достаётся: её место возвращается прежним, как у пунктов меню
 * (`layout/sectionPlace.ts`, проверка ветки 12.10.2026).
 *
 * **Смена вкладки двигает только то, что под переключателем** (11.11.2026). До этого
 * «Не привязаны» на широком окне рисовала страница списка (`ThreadsPage`): заголовок — своей
 * карточкой, переключатель — в карточке ниже, и под указателем он уезжал на 21–55 px
 * (разведка экранов 10.11.2026). Теперь у обеих вкладок одна сетка и одна карточка с одной
 * шапкой (`ThreadList`): на «Не привязаны» карточка занимает обе колонки, а шапка остаётся в
 * первой — той же ширины, на том же месте (`glass.css`, `threadsSplit[data-whole]`).
 */

import { Card, Stack } from '@mantine/core';
import { useEffect, useRef } from 'react';
import {
  NavigationType,
  Outlet,
  useLocation,
  useMatch,
  useNavigationType,
  useSearchParams,
} from 'react-router-dom';

import { partOf } from '../api/stages';
import { useSplit } from '../layout/split';
import { ThreadList } from './ThreadList';
import { ThreadsPage } from './ThreadsPage';
import { readThreadTab, rememberThreadsPlace, storedThreadsPlace } from './threadTabs';
import type { Part } from '../api/stages';

/** Что маршрут переписки знает о своём месте (`ThreadPage`). */
export interface ThreadOutlet {
  /** Переписка — правой колонкой рядом со списком, а не отдельной страницей. */
  split: boolean;
}

const SPLIT: ThreadOutlet = { split: true };
const PAGE: ThreadOutlet = { split: false };

export function ThreadsScreen() {
  const split = useSplit();
  const opened = useMatch('/threads/:id') !== null;
  const [params] = useSearchParams();
  const location = useLocation();
  const tab = readThreadTab(params);
  const part = partOf(params);
  const here = `${location.pathname}${location.search}`;
  const navigation = useNavigationType();
  const last = useRef<{ part: Part | null; pathname: string; was: string | null } | null>(null);
  useEffect(() => {
    if (tab !== 'threads') return;
    const before = last.current;
    // Переписка поправила часть заменой адреса на том же экране — прежней части адрес
    // достался по ошибке: её место — как было до него.
    if (
      before !== null &&
      navigation === NavigationType.Replace &&
      before.pathname === location.pathname &&
      before.part !== part
    ) {
      rememberThreadsPlace(before.was, before.part);
    }
    last.current = { part, pathname: location.pathname, was: storedThreadsPlace(part) };
    rememberThreadsPlace(here, part);
  }, [tab, here, part, navigation, location.pathname]);
  if (!split) return opened ? <Outlet context={PAGE} /> : <ThreadsPage />;
  // Открытая переписка сильнее вкладки из адреса: `/threads/42?tab=unbound` — переписка.
  const whole = !opened && tab === 'unbound';
  return (
    <div className="threadsSplit" data-whole={whole || undefined}>
      <Card className="glassPanel threadsSplitList" p="md">
        <ThreadList tab={whole ? 'unbound' : 'threads'} />
      </Card>
      {whole ? null : (
        <Stack className="threadsSplitPane" gap="md">
          <Outlet context={SPLIT} />
        </Stack>
      )}
    </div>
  );
}
