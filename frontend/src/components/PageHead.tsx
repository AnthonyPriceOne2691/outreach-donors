/**
 * Заголовок экрана одной строкой: название, рядом — число, если оно есть, и значок
 * «i» с пояснением, как экран устроен.
 *
 * Аудит экранов 09.10.2026: под заголовком почти каждого экрана стоял абзац в три
 * строки (на телефоне — в пять-шесть) о том, как экран устроен, и первое, по чему
 * решают, уезжало вниз. Пояснение читают раз, а не на каждом заходе: ему место в «i»
 * (Anthony: «что-то можно свернуть, показать всплывающим маленьким окошком»). Под
 * заголовком остаются только факты — числа и вывод.
 */

import { Group, Title } from '@mantine/core';
import type { ReactNode } from 'react';

import { InfoHint } from './InfoHint';

interface Props {
  title: string;
  /** Как экран устроен — словами; в подсказке «i» рядом с заголовком. */
  hint?: ReactNode;
  /** Рядом с названием: «всего 24», значок состояния. */
  children?: ReactNode;
}

export function PageHead({ title, hint, children }: Props) {
  return (
    <Group gap="sm" align="center" wrap="nowrap">
      <Title order={3}>{title}</Title>
      {children}
      {hint !== undefined && (
        <InfoHint name={`Как устроен экран «${title}»`} width={360}>
          {hint}
        </InfoHint>
      )}
    </Group>
  );
}
