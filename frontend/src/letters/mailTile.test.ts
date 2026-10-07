/**
 * Плитка «Почта» словами: до 07.10.2026 в ней стоял код транспорта
 * («sendgrid»), а пояснение «подключается на рабочем сервере» обещало то,
 * чего экран знать не может.
 */

import { describe, expect, it } from 'vitest';

import { mailTile } from './mailTile';

describe('плитка «Почта»', () => {
  it('настоящая — имя платформы и «письма уходят»', () => {
    expect(mailTile({ name: 'sendgrid', real: true, problem: null })).toEqual({
      value: 'SendGrid',
      hint: 'письма уходят',
    });
  });

  it('незнакомая настоящая — словами, а не кодом', () => {
    expect(mailTile({ name: 'mailer-x', real: true, problem: null }).value).toBe('подключена');
  });

  it('нулевой транспорт — проверочная почта', () => {
    expect(mailTile({ name: 'null', real: false, problem: null })).toEqual({
      value: 'не подключена',
      hint: 'проверочная почта, письма не уходят',
    });
  });

  it('не собравшаяся — не проверочная: причина в плашке ниже', () => {
    expect(mailTile({ name: '—', real: false, problem: 'ключа нет' })).toEqual({
      value: 'не подключена',
      hint: 'письма не уходят',
    });
  });
});
