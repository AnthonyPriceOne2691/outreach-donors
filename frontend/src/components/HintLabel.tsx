/**
 * Подпись поля со значком «i»: пояснение — в подсказке, а не строкой под полем.
 *
 * Аудит экранов 09.10.2026: пояснения под полями («Каждое стоит вызова модели», «после
 * первого письма») держали у каждого поля ряда две строки высоты и раздували поле до
 * ширины пояснения — 180 px под «50». Подпись — не `<label>`: в ней кнопка подсказки,
 * а кнопка внутри подписи вошла бы в имя поля. Полю имя даёт `aria-label` словами
 * подписи — так же, как у порогов. Ставится с `labelProps={{ labelElement: 'div' }}`.
 */

import { Group } from '@mantine/core';
import type { ReactNode } from 'react';

import { InfoHint } from './InfoHint';

export function HintLabel({ label, hint }: { label: string; hint: ReactNode }) {
  return (
    <Group component="span" gap={4} wrap="nowrap">
      {label}
      <InfoHint name={`Что значит «${label}»`} width={280}>
        {hint}
      </InfoHint>
    </Group>
  );
}
