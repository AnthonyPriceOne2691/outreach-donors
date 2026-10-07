/**
 * Отказ сервера на «Сохранить» — словами, над формой: введённое остаётся на месте,
 * а причину человек читает там, где правит. Одно место на окна и формы раздела:
 * три одинаковые плашки разошлись бы на первой правке одной из них.
 */

import { Alert } from '@mantine/core';

import { refusalOf } from '../api/client';

export function SaveRefusal({ error }: { error: Error | null }) {
  if (error === null) return null;
  return (
    <Alert color="red" title="Не сохранили">
      {refusalOf(error)}
    </Alert>
  );
}
