/**
 * Переключатель этапа: донорам или рекламодателям.
 *
 * Один на все экраны, где этап выбирают (письма, агент переписки): подписи
 * этапов и вид переключателя не расходятся между экранами. Под ним — что
 * экран делает на выбранном этапе, словами человека.
 */

import { SegmentedControl, Stack, Text } from '@mantine/core';
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
  return (
    <Stack gap={6}>
      <SegmentedControl
        aria-label={label}
        value={value}
        onChange={(picked) => onChange(picked as S)}
        data={stages ?? STAGES}
        style={{ alignSelf: 'flex-start' }}
      />
      <Text size="sm" c="dimmed" maw={680}>
        {lead}
      </Text>
    </Stack>
  );
}
