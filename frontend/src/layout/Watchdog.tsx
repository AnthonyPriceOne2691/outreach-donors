import { Alert, Stack } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { fetchWatchdog } from '../api/settings';
import { stageShown } from '../api/stages';
import { useSession } from '../auth/AuthProvider';

/**
 * Тревоги сторожа тишины — на главной, а не в отдельном разделе: поломка
 * этого класса не показывает себя нигде, и человек не пойдёт её искать.
 *
 * **Когда тихо, сторожа на экране нет.** Карточка «Сторож тишины» с текстом
 * «тихо и правильно» стояла на главной всегда и ничего не сообщала
 * (замечание 25.09.2026: «убрать плашку сторожа с главной»). Сторож
 * по-прежнему спрашивается каждые пять минут, и тревога встаёт красной
 * полосой над сводкой — появление полосы и есть сигнал.
 *
 * Своим файлом с 10.10.2026: «Обзор» упёрся в предел длины файла.
 */
export function Watchdog() {
  const { can } = useSession();
  const { data } = useQuery({
    queryKey: ['watchdog'],
    queryFn: fetchWatchdog,
    enabled: can('view'),
    // Тревоги меняются часами, а не секундами: спрашивать чаще незачем.
    refetchInterval: 5 * 60 * 1000,
  });

  // Тревога о почте продаж — только с правом «Продажи» (П2б), даже пришедшая раньше,
  // чем право сняли; общая тревога (`stage: null`) — всем.
  const alarms = (data?.alarms ?? []).filter(
    (alarm) => alarm.stage === null || stageShown(alarm.stage, can('sales')),
  );
  if (!can('view') || alarms.length === 0) return null;

  return (
    <Stack gap="sm">
      {alarms.map((alarm) => (
        <Alert key={alarm.code} color="red" title={alarm.title}>
          {alarm.detail}
        </Alert>
      ))}
    </Stack>
  );
}
