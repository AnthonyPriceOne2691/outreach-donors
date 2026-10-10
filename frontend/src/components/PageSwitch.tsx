/**
 * Переключатель страниц таблицы — один на все экраны.
 *
 * До 28.09.2026 он жил тремя копиями — история прогонов, доноры, отбор, —
 * каждая со своими подписями стрелок, и очередь форм стала бы четвёртой.
 * Три копии уже разошлись: у истории на телефоне не было правила соседей.
 *
 * **Одна страница — переключателя нет вовсе.** Он не отрисовывается,
 * а не прячется атрибутом: переключать нечего.
 *
 * **Номер — в своём элементе.** По кругу кнопки замер контраста делит
 * заливку и фон вокруг круга, а не цифру и заливку (1,28 : 1 цифре, которая
 * читается чисто), — мерить надо саму цифру.
 *
 * **На телефоне — без соседей текущей:** с ними девять кнопок не влезали
 * в строку, и «54 ›» уезжали на вторую (урок доноров).
 *
 * Страница живёт в адресе (`?page=2`): номер переживает обновление и
 * «назад», а размер страницы называет сервер.
 */

import { Box, Group, Pagination } from '@mantine/core';
import type { BoxProps } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

/** Стрелки — значки без текста; имя им даёт подпись. */
const CONTROL_NAMES: Record<string, string> = {
  previous: 'Предыдущая страница',
  next: 'Следующая страница',
  first: 'Первая страница',
  last: 'Последняя страница',
};

interface Props extends BoxProps {
  /** Имя навигации — чьи это страницы: «Страницы отбора». */
  label: string;
  page: number;
  pages: number;
  onChange: (page: number) => void;
}

export function PageSwitch({ label, page, pages, onChange, ...box }: Props) {
  const phone = useMediaQuery('(max-width: 30em)') === true;
  if (pages <= 1) return null;
  return (
    <Box component="nav" aria-label={label} {...box}>
      <Group justify="center">
        <Pagination
          value={Math.min(page, pages)}
          onChange={onChange}
          total={pages}
          siblings={phone ? 0 : 1}
          radius="xl"
          getItemProps={(number) => ({
            'aria-label': `Страница ${number}`,
            children: <span data-page-number>{number}</span>,
          })}
          getControlProps={(control) => ({ 'aria-label': CONTROL_NAMES[control] })}
        />
      </Group>
    </Box>
  );
}

/** Номер страницы — из адреса. Негодный или пустой — первая: ссылка
 *  с опечаткой в номере не должна ронять экран. Для экранов, у которых
 *  в адресе нет ничего, кроме страницы; где есть фильтры, страница живёт
 *  вместе с ними (`selectionFilters.ts`, `donorFilters.ts`).
 *
 *  `name` — имя в адресе, когда листает не весь экран, а одна карточка на нём
 *  («Бизнесы ниши» на экране рекламодателей — `?niche_page=2`): общий `page`
 *  достался бы и соседней карточке, начни она листаться. */
export function usePageParam(name = 'page'): [number, (next: number, replace?: boolean) => void] {
  const [params, setParams] = useSearchParams();
  const asked = Number(params.get(name));
  const page = Number.isInteger(asked) && asked >= 1 ? asked : 1;
  const goTo = useCallback(
    (next: number, replace = false) => {
      // Переход на ту же страницу — не переход: лишняя запись в истории
      // заставила бы нажимать «назад» дважды.
      if (next === page) return;
      setParams(
        (was) => {
          const moved = new URLSearchParams(was);
          // Первая страница — без номера: адрес экрана тот же, что в меню.
          if (next <= 1) moved.delete(name);
          else moved.set(name, String(next));
          return moved;
        },
        { replace },
      );
    },
    [name, page, setParams],
  );
  return [page, goTo];
}
