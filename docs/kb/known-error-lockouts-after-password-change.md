---
title: Known error: lockouts after a password change
category: Known errors
keywords: known error, problem, lockout, password change, mobile, workaround
---
**Status:** known error, permanent fix in progress (problem record linked to this article).

## Symptom

After changing their password, a person's account locks again and again, often every few minutes,
even though they type the new password correctly.

## Cause

A phone or second device keeps trying the old password: mail apps and Teams on phones retry in the
background. Ten failures lock the account.

## Workaround (service desk)

1. Unlock the account.
2. Find the source: run `Get-LabLockoutSource -Identity <user>` on the management server. It reads
   the domain controller's lockout events (4740) and failed sign-ins (4771) and names the device.
3. Ask the person to remove and re-add their account on that device (see **Your account keeps
   locking**).
4. Link the incident to the problem record.

## Permanent fix

Phones are moving to Microsoft Intune with modern authentication, which uses tokens instead of a
saved password, so a password change no longer breaks them.
