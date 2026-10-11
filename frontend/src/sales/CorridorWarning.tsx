/**
 * «Коридор отличия трудно достижим» — жёлтой строкой у первого письма цепочки.
 *
 * Не отказ: шаг записан, письма выйдут, но сборка будет переписывать каждое по нескольку
 * попыток — каждая стоит вызова модели. Причина, совет и доля переписываемого — словами
 * сервера (`chain_text.corridor_warning`), одним текстом для экрана и консоли загрузки.
 * Вид — плашкой в строку, как «Модуль продаж выключен» (`ModuleOff`): янтарь — «нужно
 * внимание».
 */

import { Alert, Text } from '@mantine/core';

export function CorridorWarning({ text }: { text: string }) {
  return (
    <Alert color="yellow" py={6} px="sm" className="chainWarning">
      <Text size="sm">{text}</Text>
    </Alert>
  );
}
