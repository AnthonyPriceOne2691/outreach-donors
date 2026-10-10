/**
 * Несохранённое не пропадает молча: уход со страницы — с вопросом.
 *
 * Проверка прода 10.10.2026, «Агент переписки»: набранное в настройках пропадало
 * без слова при уходе через меню и при обновлении вкладки.
 *
 * **Закрытие, обновление вкладки и адрес, набранный руками** — `beforeunload`:
 * браузер спрашивает своим окном, и его текст страница не выбирает.
 *
 * **Уход по ссылке внутри приложения** — меню, «Сменить пароль», любая ссылка на
 * другой экран — окном здесь. `useBlocker` React Router работает только с роутером
 * данных (`createBrowserRouter`), а приложение стоит на `BrowserRouter`: переводить
 * весь роутинг и оснастку тестов на другой роутер ради одного вопроса — несоразмерно.
 * Поэтому щелчок по ссылке на другой экран ловится до React (фаза захвата на
 * `document`) и гасится `preventDefault`: `Link` перехода не делает, если событие
 * уже погашено, а меню и прочие обработчики щелчка отрабатывают как обычно. После
 * «Уйти без сохранения» переход делает сам экран.
 *
 * Не держит то, что идёт мимо ссылок: «Назад» и «Вперёд» браузера и переход кодом
 * (`navigate` по кнопке) — для них нужен роутер данных. На экранах, где это стоит,
 * таких переходов нет; выход из учётки — решение человека, и он не держится.
 */

import { Button, Group, Modal, Stack, Text } from '@mantine/core';
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

/** Щелчок с клавишей открывает ссылку рядом, а не уводит со страницы. */
function withKeys(event: MouseEvent): boolean {
  return event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
}

/** Ссылка, которая уводит эту вкладку: не в новую и не скачиванием. */
function inThisTab(link: HTMLAnchorElement): boolean {
  return (link.target === '' || link.target === '_self') && !link.hasAttribute('download');
}

/** Куда уводит щелчок: путь другого экрана приложения. `null` — не уход: ссылка
 *  в новую вкладку или на чужой сайт (её спросит `beforeunload`), щелчок с клавишей,
 *  ссылка на этот же экран. */
export function leavingTo(event: MouseEvent, here: string): string | null {
  if (event.defaultPrevented || event.button !== 0 || withKeys(event)) return null;
  const link = event.target instanceof Element ? event.target.closest('a[href]') : null;
  if (!(link instanceof HTMLAnchorElement) || !inThisTab(link)) return null;
  const url = new URL(link.href, window.location.href);
  if (url.origin !== window.location.origin || url.pathname === here) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}

interface Props {
  /** Что не сохранено — словами для окна; `null` — сохранять нечего, уход без вопроса. */
  unsaved: string | null;
}

export function LeaveGuard({ unsaved }: Props) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [target, setTarget] = useState<string | null>(null);
  const dirty = unsaved !== null;

  useEffect(() => {
    if (!dirty) return undefined;
    const onClick = (event: MouseEvent) => {
      const to = leavingTo(event, pathname);
      if (to === null) return;
      event.preventDefault();
      setTarget(to);
    };
    const onUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // Safari спрашивает только по `returnValue`, остальные — по `preventDefault`.
      event.returnValue = '';
    };
    document.addEventListener('click', onClick, true);
    window.addEventListener('beforeunload', onUnload);
    return () => {
      document.removeEventListener('click', onClick, true);
      window.removeEventListener('beforeunload', onUnload);
    };
  }, [dirty, pathname]);

  const leave = () => {
    setTarget(null);
    if (target !== null) void navigate(target);
  };

  return (
    <Modal opened={target !== null} onClose={() => setTarget(null)} title="Уйти без сохранения?">
      <Stack gap="md">
        <Text size="sm">{unsaved}</Text>
        {/* Фокус — на «Остаться»: Enter после щелчка по меню не стирает набранное. */}
        <Group justify="flex-end" gap="sm">
          <Button
            variant="default"
            className="press"
            data-autofocus
            onClick={() => setTarget(null)}
          >
            Остаться
          </Button>
          <Button color="red" className="press" onClick={leave}>
            Уйти без сохранения
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
