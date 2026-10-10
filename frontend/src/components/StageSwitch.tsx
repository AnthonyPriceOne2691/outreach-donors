/**
 * Переключатель этапа: донорам или рекламодателям.
 *
 * Один на все экраны, где этап выбирают (письма, агент переписки): подписи
 * этапов и вид переключателя не расходятся между экранами. Под ним — что
 * экран делает на выбранном этапе, словами человека.
 *
 * **На телефоне — столбиком во всю ширину**, как период воронки продаж
 * (`sales/FunnelPane`). В ряд «Донорам · Рекламодателям · Бизнесам ниши» —
 * около 360 px, а панели на телефоне достаётся 286: на 390 px третий этап
 * уходил за край экрана и срезался (проверка QA 10.10.2026, «Письма»). Столбик,
 * а не список: этапов два-три, и все видны и нажимаются сразу.
 */

import { SegmentedControl, Stack, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import type { ReactNode } from 'react';

import type { LetterStage } from '../api/types';

/** Этап на выбор: значение и подпись. */
export interface StageOption<S extends string> {
  value: S;
  label: string;
}

const STAGES: StageOption<LetterStage>[] = [
  { value: 'donors', label: 'Донорам' },
  { value: 'advertisers', label: 'Рекламодателям' },
];

/** Узкое окно — то же, что у вкладок «Диалогов» и периода воронки. */
const NARROW = '(max-width: 36em)';

interface StageSwitchProps<S extends string> {
  /** Имя переключателя для программ чтения с экрана и тестов. */
  label: string;
  value: S;
  onChange: (stage: S) => void;
  /** Пояснение к выбранному этапу. */
  lead: ReactNode;
  /** Этапы на выбор; нет — доноры и рекламодатели. Экран агента берёт их из
   *  реестра сервера: новый этап встаёт на него без правки экрана. */
  stages?: StageOption<S>[];
}

export function StageSwitch<S extends string = LetterStage>({
  label,
  value,
  onChange,
  lead,
  stages,
}: StageSwitchProps<S>) {
  // Значение — с первой отрисовки: иначе телефон на миг рисовал бы ряд за краем
  // экрана и перестраивал его на глазах (как `layout/split`).
  const narrow = useMediaQuery(NARROW, false, { getInitialValueInEffect: false }) === true;
  return (
    <Stack gap={6}>
      <SegmentedControl
        aria-label={label}
        value={value}
        onChange={(picked) => onChange(picked as S)}
        data={stages ?? STAGES}
        orientation={narrow ? 'vertical' : 'horizontal'}
        fullWidth={narrow}
        style={{ alignSelf: narrow ? 'stretch' : 'flex-start' }}
      />
      <Text size="sm" c="dimmed" maw={680}>
        {lead}
      </Text>
    </Stack>
  );
}
