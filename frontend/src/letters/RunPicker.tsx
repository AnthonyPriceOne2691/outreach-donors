/**
 * Из каких прогонов собирается рассылка.
 *
 * **Рассылка — из принятых доноров выбранных прогонов, а не из всей базы.**
 * Из всей базы в одну рассылку попадали бы сайты ставок из ЮАР рядом
 * с SaaS из США, а письмо у рассылки одно. Страна письма берётся из
 * прогонов; прогоны разных стран сервер не соберёт и скажет почему.
 *
 * Показываются только прогоны, где уже есть принятые: из остальных
 * собирать нечего, и выбор такого прогона выглядел бы как поломка.
 *
 * **Список свой, а не страница истории.** Раньше выбор брал общий список
 * прогонов и отсеивал его здесь, а общий список отдавал последние тридцать;
 * с историей по десять на странице старый прогон с принятыми пропал бы
 * из выбора молча. Теперь сервер отдаёт ровно такие прогоны и все сразу,
 * а ключ кэша свой: страница истории его не затирает, а решение
 * на рассмотрении (`['runs']`) обновляет оба.
 */

import { Checkbox, Group, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { countryTitle } from '../api/labels';
import { listRunsWithAccepted } from '../api/runs';

interface Props {
  value: number[];
  onChange: (next: number[]) => void;
}

export function RunPicker({ value, onChange }: Props) {
  const { data } = useQuery({ queryKey: ['runs', 'with-accepted'], queryFn: listRunsWithAccepted });
  const ready = data ?? [];

  if (ready.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Принятых доноров ещё нет: рассылка собирается из прогонов, в которых кого-то приняли на
        рассмотрении.
      </Text>
    );
  }
  return (
    <Stack gap={6}>
      <Text size="sm" fw={500}>
        Прогоны рассылки
      </Text>
      <Checkbox.Group value={value.map(String)} onChange={(next) => onChange(next.map(Number))}>
        <Group gap="md">
          {ready.map((run) => (
            <Checkbox
              key={run.id}
              value={String(run.id)}
              label={`№${run.id} · ${countryTitle(run.country)} · принято ${run.queue.accepted}`}
            />
          ))}
        </Group>
      </Checkbox.Group>
    </Stack>
  );
}
