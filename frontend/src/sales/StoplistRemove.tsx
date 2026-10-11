/**
 * Снятие записи стоп-листа продаж — с подтверждением: что станет с лидами, сказано до нажатия.
 * Лиды, которых закрывала только эта запись, сервер возвращает в «новые» — итог называет их
 * число; лиды и счётчики раздела перечитываются.
 */

import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { removeFromSalesStoplist } from '../api/salesStoplist';
import type { SalesStopRow } from '../api/salesStoplist';
import { formatNumber } from '../format';
import { notify } from '../notices';

/** Подтверждение снятия: что станет с лидами — до нажатия. */
export function ConfirmRemove({
  row,
  busy,
  error,
  onKeep,
  onRemove,
}: {
  row: SalesStopRow;
  busy: boolean;
  error: Error | null;
  onKeep: () => void;
  onRemove: () => void;
}) {
  return (
    <Modal
      opened
      onClose={onKeep}
      title={`Снять ${row.host ?? row.email ?? ''} со стоп-листа продаж`}
    >
      <Stack gap="sm">
        <Text size="sm">
          Лиды, которых закрывала только эта запись, вернутся в «новые» — до писем их проверит
          очистка.
        </Text>
        {error !== null && (
          <Alert color="red" title="Не сняли">
            {refusalOf(error)}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" data-autofocus onClick={onKeep}>
            Оставить
          </Button>
          <Button color="red" className="press" loading={busy} onClick={onRemove}>
            Снять запись
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export function useRemove(close: () => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (row: SalesStopRow) => removeFromSalesStoplist(row.id),
    onSuccess: async (removed) => {
      close();
      const back =
        removed.released > 0
          ? ` Лидов вернулось на очистку: ${formatNumber(removed.released)}.`
          : '';
      notify({ message: `${removed.target} снят со стоп-листа продаж.${back}`, color: 'yellow' });
      await client.invalidateQueries({ queryKey: ['sales'] });
    },
  });
}
