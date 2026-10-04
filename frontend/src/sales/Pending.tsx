/**
 * Раздел продаж ещё не прочитан: крутилка до первого ответа, отказ — словами.
 * Одно место на обе страницы раздела — список гипотез нужен и экрану, и мастеру.
 */

import { Alert, Loader } from '@mantine/core';

import { refusalOf } from '../api/client';

export function Pending({ error }: { error: unknown }) {
  if (error) {
    return (
      <Alert color="red" title="Раздел продаж не загрузился" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }
  return <Loader aria-label="Загружаем раздел продаж" m="md" />;
}
