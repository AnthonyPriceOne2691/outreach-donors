/**
 * Строка плана над таблицей гипотез: «План «…»: готово 3 пилота из 10 · расход 12,40 $ из 50,00 $»,
 * «Остановить план» или «Продолжить» и «Потолок» — окно «Потолок плана, $» (`PlanCap`): потолок
 * меняется и после сохранения, поднятый снимает паузу плана, вставшего на своём потолке.
 *
 * Строка — у идущего плана и у плана на паузе; окончённый своей строки не держит: его гипотезы
 * видны в колонке «План работ», и «Собрать ещё» там же пускает его снова. **С паузы план снимает
 * только кнопка**: конвейер сам не продолжает план, вставший на потолке, счёте выдачи или модели, —
 * почему стоит, видно словами сервера рядом. Отказ кнопки — словами под строкой, со своей подписью
 * «План не продолжен: …»: красная причина паузы и красный отказ подряд читались одним абзацем
 * (стенд 17.11). Слова сервера рвутся где угодно — длинное слово уходило за край панели на 390.
 */

import { Button, Group, Stack, Text } from '@mantine/core';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { capitalized } from '../format';
import { resumePlan, stopPlan, usePlanAction } from './planApi';
import type { PlanLive } from './planApi';
import { PlanCap } from './PlanCap';
import { planWords } from './planWords';

function PlanLine({ plan }: { plan: PlanLive }) {
  const act = usePlanAction((stop: boolean) => (stop ? stopPlan(plan.id) : resumePlan(plan.id)));
  const [capping, setCapping] = useState(false);
  const running = plan.status === 'running';
  return (
    <Stack gap={2}>
      <Group gap="sm" wrap="wrap" align="center">
        <Text size="sm" fw={500}>
          {planWords(plan)}
        </Text>
        <Button
          size="compact-sm"
          variant="default"
          className="press"
          loading={act.isPending}
          aria-label={`${running ? 'Остановить' : 'Продолжить'} план «${plan.name}»`}
          onClick={() => act.mutate(running)}
        >
          {running ? 'Остановить план' : 'Продолжить'}
        </Button>
        <Button
          size="compact-sm"
          variant="subtle"
          className="press"
          aria-label={`Потолок плана «${plan.name}»`}
          onClick={() => setCapping(true)}
        >
          Потолок
        </Button>
      </Group>
      {capping && <PlanCap plan={plan} onClose={() => setCapping(false)} />}
      {!running && (
        <Text size="sm" c="red">
          {capitalized(plan.problem ?? 'план на паузе')}
        </Text>
      )}
      {act.error !== null && (
        <Text size="sm" c="red" role="alert">
          {act.variables === true ? 'План не остановлен' : 'План не продолжен'}:{' '}
          {refusalOf(act.error)}
        </Text>
      )}
    </Stack>
  );
}

/** Строки планов, которые идут или стоят, — новые сверху, как в списке планов. Планы не пришли —
 *  строкой словами сервера: таблица гипотез остаётся, без колонки плана. */
export function PlanLines({
  plans,
  error,
}: {
  plans: PlanLive[] | undefined;
  error: Error | null;
}) {
  const shown = (plans ?? []).filter((plan) => plan.status !== 'done');
  if (shown.length === 0 && error === null) return null;
  return (
    <Stack gap="xs" px="md" style={{ overflowWrap: 'anywhere' }}>
      {error !== null && (
        <Text size="sm" c="red" role="alert">
          Планы работ не загрузились: {refusalOf(error)}
        </Text>
      )}
      {shown.map((plan) => (
        <PlanLine key={plan.id} plan={plan} />
      ))}
    </Stack>
  );
}
