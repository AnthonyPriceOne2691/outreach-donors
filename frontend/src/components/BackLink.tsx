/**
 * Возврат к списку — стрелка влево над заголовком карточки.
 *
 * Замечание 25.09.2026 про карточку прогона: «слева над заголовком — кнопка
 * „К прогонам“, как „К списку“ у доноров». Одно действие стоит в одном месте
 * на всех экранах: карточка донора получила тот же возврат на том же месте,
 * иначе два экрана одного сервиса отвечали бы на «где назад» по-разному.
 *
 * **Ссылка, а не кнопка с переходом:** её открывают и в новой вкладке, а
 * программа чтения с экрана называет её ссылкой — это и есть переход.
 *
 * **Возвращает туда, откуда пришли.** Список держит страницу и фильтры
 * в адресе и отдаёт их сюда в состоянии перехода (`state.from`); пришли
 * не из списка — ведёт на его начало.
 */

import { Button } from '@mantine/core';
import { IconArrowLeft } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

/** Строка параметров списка, из которого пришли, — или пусто. Чужое
 *  содержимое состояния (не строка параметров) не принимается: адрес
 *  списка собирается только из того, что список сам туда положил. */
export function backTo(state: unknown): string {
  if (state && typeof state === 'object' && 'from' in state && typeof state.from === 'string') {
    return state.from.startsWith('?') ? state.from : '';
  }
  return '';
}

interface Props {
  to: string;
  children: ReactNode;
}

export function BackLink({ to, children }: Props) {
  return (
    <Button
      component={Link}
      to={to}
      variant="subtle"
      size="compact-sm"
      className="press backLink"
      leftSection={<IconArrowLeft size={16} />}
    >
      {children}
    </Button>
  );
}
