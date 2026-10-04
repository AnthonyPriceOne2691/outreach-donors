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

const STAGES: { value: LetterStage; label: string }[] = [
  { value: 'donors', label: 'Донорам' },
  { value: 'advertisers', label: 'Рекламодателям' },
];

interface StageSwitchProps {
  /** Имя переключателя для программ чтения с экрана и тестов. */
  label: string;
  value: LetterStage;
  onChange: (stage: LetterStage) => void;
  /** Пояснение к выбранному этапу. */
  lead: ReactNode;
}

export function StageSwitch({ label, value, onChange, lead }: StageSwitchProps) {
  return (
    <Stack gap={6}>
      <SegmentedControl
        aria-label={label}
        value={value}
        onChange={(picked) => onChange(picked as LetterStage)}
        data={STAGES}
        style={{ alignSelf: 'flex-start' }}
      />
      <Text size="sm" c="dimmed" maw={680}>
        {lead}
      </Text>
    </Stack>
  );
}
