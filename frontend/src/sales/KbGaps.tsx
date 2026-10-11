/**
 * Пустые разделы базы знаний — строкой у каждого пустого раздела языка писем: что агент сделает
 * без него («Кейсов на английском нет — агент не приведёт ни одного примера»).
 *
 * Агент работает и при пустых кейсах или ценах — промпт велит не называть цен, судья ловит
 * выдуманное, — но экран об этом молчал. Слова и правило — сервера (`kb_gaps.py`, тем же
 * правилом, каким агент берёт записи: нет на языке письма — берёт на другом и переводит).
 * Все записи выключены — одна строка, а не четырнадцать: агент не видит ни одной.
 */

import { Alert, List } from '@mantine/core';

import type { KbView } from '../api/salesTypes';

export function KbGaps({ view }: { view: KbView }) {
  if (view.total === 0 || view.gaps.length === 0) return null;
  if (view.active === 0) {
    return (
      <Alert color="yellow" title="Агент не видит ни одной записи" mx="md">
        Все записи выключены — агент пишет без базы знаний: без услуг, кейсов и цен.
      </Alert>
    );
  }
  return (
    <Alert color="yellow" title="Пустые разделы" mx="md">
      <List size="sm" spacing={2}>
        {view.gaps.map((gap) => (
          <List.Item key={`${gap.language}:${gap.kind}`}>{gap.words}.</List.Item>
        ))}
      </List>
    </Alert>
  );
}
