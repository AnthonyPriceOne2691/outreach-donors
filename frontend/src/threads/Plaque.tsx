/**
 * Плашка разбора ответа одним абзацем: жирное «что» и обычное «почему».
 *
 * Так говорят разбор цены и «Цена та же»: ответ ждёт человека — жёлтым, отказ сервера —
 * красным, его словами. Плашка стоит у полей, к которым относится: уведомление в углу
 * закрывало «Подтвердить» и «Не продаёт размещения» (проверка QA 10.10.2026), а рядом
 * со списком переписка — колонка высотой окна, и второй строки в ней нет.
 */

import { Alert, Text } from '@mantine/core';
import type { CSSProperties, ReactNode } from 'react';

export function Plaque({
  color,
  title,
  style,
  children,
}: {
  color: 'red' | 'yellow';
  /** Что случилось — жирным. */
  title: string;
  style?: CSSProperties;
  /** Почему и что делать — следом, тем же абзацем. */
  children: ReactNode;
}) {
  return (
    <Alert color={color} py={6} px="sm" style={style}>
      <Text span size="sm" fw={600}>
        {title}
      </Text>{' '}
      <Text span size="sm">
        {children}
      </Text>
    </Alert>
  );
}
